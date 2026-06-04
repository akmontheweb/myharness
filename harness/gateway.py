"""
Model-agnostic LLM Gateway with prefix caching, token tracking, budget enforcement,
and exponential backoff for all provider API calls.

This module implements:
    - BaseLLM abstract interface for any provider (OpenAI, Anthropic, DeepSeek, Ollama)
    - Provider-specific HTTP clients with async httpx transport
    - Token usage extraction parsers for each provider's response payload shape
    - Prefix caching anchor utility — ensures system prompts are locked at messages[0]
    - Pre-flight context window guardrail (85% threshold with aggressive truncation)
    - Token-budget-aware dispatch: refuses calls when budget_remaining_usd <= 0
    - Exponential backoff with random jitter for HTTP 429 rate limit handling
    - Model auto-selection based on node role and .harness_config.json routing rules
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Data Types
# ---------------------------------------------------------------------------

class NodeRole(Enum):
    """Identifies which graph node is making the LLM call."""
    PLANNING = "planning"
    PATCHING = "patching"
    REPAIR = "repair"
    HUMAN_INTERVENTION = "human_intervention"


@dataclass
class TokenUsage:
    """Extracted token usage metadata from a single LLM response."""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    model_name: str = ""
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "model_name": self.model_name,
            "cost_usd": self.cost_usd,
        }


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    content: str
    usage: TokenUsage
    model: str
    finish_reason: str = "stop"
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelSpec:
    """Specification for a model including cost rates and context window limits."""
    provider: str  # "deepseek", "anthropic", "openai", "ollama"
    model_id: str
    context_window: int  # maximum tokens the model accepts
    input_cost_per_1m: float  # cost per 1M input tokens in USD
    output_cost_per_1m: float  # cost per 1M output tokens in USD
    cached_input_cost_per_1m: float = 0.0  # cached/prompt-cache discount
    api_base_url: str = ""
    supports_thinking: bool = False
    supports_cache: bool = False


# Model registry — user-populated via register_model() or .harness_config.json.
# No default models are bundled. Every model must be explicitly registered.
_MODEL_REGISTRY: dict[str, ModelSpec] = {}


def get_model_spec(model_key: str) -> Optional[ModelSpec]:
    """
    Look up a model specification by its canonical key.

    Returns None if the model is not registered. All models must be explicitly
    registered via register_model() or .harness_config.json before use.
    """
    return _MODEL_REGISTRY.get(model_key)


def register_model(model_key: str, spec: ModelSpec) -> None:
    """
    Register a model specification in the global registry.

    Args:
        model_key: Canonical key (e.g., 'openai:gpt-4o', 'anthropic:claude-sonnet-4').
        spec: The ModelSpec with provider details, costs, and context window.
    """
    _MODEL_REGISTRY[model_key] = spec
    logger.info("[gateway] Registered model '%s' (provider=%s, ctx=%d).", model_key, spec.provider, spec.context_window)


def register_models_from_config(config_dict: dict[str, Any]) -> int:
    """
    Batch-register models from a .harness_config.json 'models' section.

    Expected config format:
        {
          "models": {
            "openai:gpt-4o": {
              "provider": "openai",
              "model_id": "gpt-4o",
              "context_window": 128000,
              "input_cost_per_1m": 2.50,
              "output_cost_per_1m": 10.00,
              "cached_input_cost_per_1m": 1.25,
              "api_base_url": "https://api.openai.com/v1"
            }
          }
        }

    Args:
        config_dict: Parsed config dictionary from .harness_config.json.

    Returns:
        Number of models registered.
    """
    models_section = config_dict.get("models", {})
    count = 0
    for model_key, spec_dict in models_section.items():
        if not isinstance(spec_dict, dict):
            logger.warning("[gateway] Skipping invalid model spec for '%s': not a dict.", model_key)
            continue
        try:
            spec = ModelSpec(
                provider=spec_dict.get("provider", model_key.split(":")[0] if ":" in model_key else "unknown"),
                model_id=spec_dict.get("model_id", model_key),
                context_window=spec_dict.get("context_window", 131072),
                input_cost_per_1m=spec_dict.get("input_cost_per_1m", 0.0),
                output_cost_per_1m=spec_dict.get("output_cost_per_1m", 0.0),
                cached_input_cost_per_1m=spec_dict.get("cached_input_cost_per_1m", 0.0),
                api_base_url=spec_dict.get("api_base_url", ""),
                supports_thinking=spec_dict.get("supports_thinking", False),
                supports_cache=spec_dict.get("supports_cache", False),
            )
            register_model(model_key, spec)
            count += 1
        except Exception as exc:
            logger.warning("[gateway] Failed to register model '%s': %s", model_key, exc)
    if count > 0:
        logger.info("[gateway] Registered %d model(s) from config.", count)
    return count


# ---------------------------------------------------------------------------
# 2. BaseLLM Abstract Interface
# ---------------------------------------------------------------------------

class BaseLLM(ABC):
    """
    Abstract base for all LLM provider clients.

    Each provider (DeepSeek, Anthropic, OpenAI, Ollama) implements:
        - chat_completion(messages, **kwargs) → LLMResponse
        - extract_usage(raw_response) → TokenUsage
        - compute_cost(usage) → float
    """

    def __init__(self, spec: ModelSpec, api_key: Optional[str] = None):
        self.spec = spec
        self.api_key = api_key or os.environ.get(f"{spec.provider.upper()}_API_KEY", "")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def provider_name(self) -> str:
        return self.spec.provider

    @property
    def model_name(self) -> str:
        return self.spec.model_id

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create and reuse an httpx AsyncClient."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.spec.api_base_url,
                timeout=httpx.Timeout(120.0, connect=10.0),
                headers=self._build_headers(),
            )
        return self._client

    def _build_headers(self) -> dict[str, str]:
        """Construct provider-specific HTTP headers."""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def close(self) -> None:
        """Release the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        thinking: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion request and return a standardized response."""
        ...

    @abstractmethod
    def extract_usage(self, raw_response: dict[str, Any]) -> TokenUsage:
        """Parse token usage metadata from the provider's raw response JSON."""
        ...

    @abstractmethod
    def compute_cost(self, usage: TokenUsage) -> float:
        """Compute USD cost based on token counts and model pricing rates."""
        ...


# ---------------------------------------------------------------------------
# 3. DeepSeek Provider Implementation
# ---------------------------------------------------------------------------

class DeepSeekProvider(BaseLLM):
    """DeepSeek API client using OpenAI-compatible /v1/chat/completions endpoint."""

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        thinking: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if thinking and self.spec.supports_thinking:
            payload["thinking"] = {"type": "enabled"}

        logger.debug("[deepseek] Sending completion request. model=%s tokens_est=%d", self.spec.model_id, len(messages))

        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        usage = self.extract_usage(data)
        usage.cost_usd = self.compute_cost(usage)

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason", "stop")

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.spec.model_id,
            finish_reason=finish_reason,
            raw_response=data,
        )

    def extract_usage(self, raw_response: dict[str, Any]) -> TokenUsage:
        usage_block = raw_response.get("usage", {})
        # DeepSeek returns prompt_tokens_details.cached_tokens when cache hits occur
        prompt_details = usage_block.get("prompt_tokens_details", {})
        return TokenUsage(
            input_tokens=usage_block.get("prompt_tokens", 0),
            output_tokens=usage_block.get("completion_tokens", 0),
            cached_tokens=prompt_details.get("cached_tokens", 0),
            model_name=self.spec.model_id,
        )

    def compute_cost(self, usage: TokenUsage) -> float:
        spec = self.spec
        # Cache-hit tokens are billed at the lower cached rate
        cached = usage.cached_tokens
        uncached_input = max(0, usage.input_tokens - cached)

        input_cost = (uncached_input / 1_000_000) * spec.input_cost_per_1m
        cached_cost = (cached / 1_000_000) * spec.cached_input_cost_per_1m
        output_cost = (usage.output_tokens / 1_000_000) * spec.output_cost_per_1m

        return input_cost + cached_cost + output_cost


# ---------------------------------------------------------------------------
# 4. Anthropic Provider Implementation
# ---------------------------------------------------------------------------

class AnthropicProvider(BaseLLM):
    """Anthropic (Claude) API client using /v1/messages endpoint."""

    def _build_headers(self) -> dict[str, str]:
        headers = super()._build_headers()
        # Anthropic uses x-api-key header instead of Authorization Bearer
        headers["x-api-key"] = self.api_key
        headers["anthropic-version"] = "2023-06-01"
        # Remove the Bearer header since Anthropic doesn't use it
        headers.pop("Authorization", None)
        return headers

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        thinking: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        client = await self._get_client()

        # Anthropic requires a system prompt separated from the messages array.
        # Extract system message(s) and pass them as the top-level 'system' field.
        system_content: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_content.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            system_content.append(block.get("text", ""))
            else:
                # Map to Anthropic message format
                anthropic_msg: dict[str, Any] = {"role": role, "content": msg.get("content", "")}
                anthropic_messages.append(anthropic_msg)

        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_content:
            # Anthropic expects a single string or list of text blocks for system
            payload["system"] = "\n\n".join(system_content)

        logger.debug("[anthropic] Sending completion request. model=%s", self.spec.model_id)

        response = await client.post("/messages", json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        usage = self.extract_usage(data)
        usage.cost_usd = self.compute_cost(usage)

        # Anthropic returns content as a list of blocks; extract text
        content_blocks = data.get("content", [])
        text_parts: list[str] = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        content = "\n".join(text_parts)

        finish_reason = data.get("stop_reason", "stop")

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.spec.model_id,
            finish_reason=finish_reason,
            raw_response=data,
        )

    def extract_usage(self, raw_response: dict[str, Any]) -> TokenUsage:
        usage_block = raw_response.get("usage", {})
        return TokenUsage(
            input_tokens=usage_block.get("input_tokens", 0),
            output_tokens=usage_block.get("output_tokens", 0),
            cached_tokens=usage_block.get("cache_read_input_tokens", 0)
            + usage_block.get("cache_creation_input_tokens", 0),
            model_name=self.spec.model_id,
        )

    def compute_cost(self, usage: TokenUsage) -> float:
        spec = self.spec
        cached = usage.cached_tokens
        uncached_input = max(0, usage.input_tokens - cached)

        input_cost = (uncached_input / 1_000_000) * spec.input_cost_per_1m
        cached_cost = (cached / 1_000_000) * spec.cached_input_cost_per_1m
        output_cost = (usage.output_tokens / 1_000_000) * spec.output_cost_per_1m

        return input_cost + cached_cost + output_cost


# ---------------------------------------------------------------------------
# 5. OpenAI Provider Implementation
# ---------------------------------------------------------------------------

class OpenAIProvider(BaseLLM):
    """OpenAI API client using /v1/chat/completions endpoint."""

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        thinking: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        logger.debug("[openai] Sending completion request. model=%s", self.spec.model_id)

        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        usage = self.extract_usage(data)
        usage.cost_usd = self.compute_cost(usage)

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason", "stop")

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.spec.model_id,
            finish_reason=finish_reason,
            raw_response=data,
        )

    def extract_usage(self, raw_response: dict[str, Any]) -> TokenUsage:
        usage_block = raw_response.get("usage", {})
        prompt_details = usage_block.get("prompt_tokens_details", {})
        return TokenUsage(
            input_tokens=usage_block.get("prompt_tokens", 0),
            output_tokens=usage_block.get("completion_tokens", 0),
            cached_tokens=prompt_details.get("cached_tokens", 0),
            model_name=self.spec.model_id,
        )

    def compute_cost(self, usage: TokenUsage) -> float:
        spec = self.spec
        input_cost = (usage.input_tokens / 1_000_000) * spec.input_cost_per_1m
        output_cost = (usage.output_tokens / 1_000_000) * spec.output_cost_per_1m
        return input_cost + output_cost


# ---------------------------------------------------------------------------
# 6. Ollama (Local) Provider Implementation
# ---------------------------------------------------------------------------

class OllamaProvider(BaseLLM):
    """Ollama local inference server using OpenAI-compatible /v1/chat/completions endpoint."""

    def _build_headers(self) -> dict[str, str]:
        # Ollama doesn't require an API key; skip Authorization header
        return {"Content-Type": "application/json"}

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        thinking: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        logger.debug("[ollama] Sending completion request. model=%s", self.spec.model_id)

        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        usage = self.extract_usage(data)
        usage.cost_usd = 0.0  # Local inference is free

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason", "stop")

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.spec.model_id,
            finish_reason=finish_reason,
            raw_response=data,
        )

    def extract_usage(self, raw_response: dict[str, Any]) -> TokenUsage:
        usage_block = raw_response.get("usage", {})
        return TokenUsage(
            input_tokens=usage_block.get("prompt_tokens", 0),
            output_tokens=usage_block.get("completion_tokens", 0),
            cached_tokens=0,
            model_name=self.spec.model_id,
        )

    def compute_cost(self, usage: TokenUsage) -> float:
        return 0.0  # Local models incur no API cost


# ---------------------------------------------------------------------------
# 7. Provider Factory
# ---------------------------------------------------------------------------

_provider_classes: dict[str, type[BaseLLM]] = {
    "deepseek": DeepSeekProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
}


def create_provider(model_key: str, api_key: Optional[str] = None) -> BaseLLM:
    """
    Factory: create the correct BaseLLM provider for a given model key.

    Args:
        model_key: Canonical model key (e.g., 'openai:gpt-4o').
        api_key: Optional API key override. Falls back to environment variable.

    Returns:
        A configured BaseLLM provider instance.

    Raises:
        ValueError: If the model is not registered or the provider is unrecognized.
    """
    spec = get_model_spec(model_key)
    if spec is None:
        raise ValueError(
            f"Model '{model_key}' is not registered. "
            f"Register it via .harness_config.json 'models' section or gateway.register_model()."
        )
    provider_name = spec.provider
    cls = _provider_classes.get(provider_name)
    if cls is None:
        raise ValueError(
            f"Unknown provider '{provider_name}' for model '{model_key}'. "
            f"Supported providers: {list(_provider_classes.keys())}"
        )
    return cls(spec, api_key=api_key)


# ---------------------------------------------------------------------------
# 8. Token Counting Utility (Pre-flight Context Window Guard)
# ---------------------------------------------------------------------------

def estimate_token_count(messages: list[dict[str, Any]]) -> int:
    """
    Fast heuristic token estimation for pre-flight context window checks.

    Uses a simple character-to-token ratio (~4 chars per token for English text)
    plus overhead for message formatting. Not exact, but fast and sufficient for
    the 85% guardrail threshold check.
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(str(block))
        total_chars += 50  # Overhead per message for role markers, formatting, etc.
    return max(1, total_chars // 4)  # ~4 chars per token is a common heuristic


# ---------------------------------------------------------------------------
# 9. Prefix Caching Anchor Utility
# ---------------------------------------------------------------------------

def ensure_prefix_cache_anchor(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Guarantee that the immutable system prompt is anchored at messages[0].

    This is critical for provider prompt caching (DeepSeek and Anthropic both
    offer discounted rates for repeated prefix content). The system prompt
    must never be moved, modified, or truncated — it stays at position 0 always.

    If the first message is not a system message, this utility logs a warning
    but does not reorder (to avoid destroying conversation semantics).
    """
    if not messages:
        return messages

    first = messages[0]
    if first.get("role") != "system":
        logger.warning(
            "[gateway] messages[0] is not a system message (role='%s'). "
            "Prefix caching discounts may not apply.",
            first.get("role"),
        )

    # Compute a content hash of the system prompt for cache-hit tracking
    if first.get("role") == "system":
        content = first.get("content", "")
        content_hash = hashlib.sha256(
            content.encode("utf-8") if isinstance(content, str) else json.dumps(content, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        logger.debug("[gateway] System prompt anchor hash: %s", content_hash)

    return messages


# ---------------------------------------------------------------------------
# 10. Context Window Guardrail & Truncation
# ---------------------------------------------------------------------------

async def check_context_window(
    messages: list[dict[str, Any]],
    spec: ModelSpec,
    threshold_pct: float = 0.85,
) -> list[dict[str, Any]]:
    """
    Pre-flight context window guardrail.

    If the estimated token count exceeds `threshold_pct` of the model's
    context window, aggressively truncate older non-system messages until
    the payload fits within the threshold.

    Truncation strategy:
        1. Always keep messages[0] (the system prompt anchor).
        2. Always keep the last user message (the current request).
        3. Drop the oldest non-system, non-current messages first.
        4. If still over threshold after dropping all trimmable messages, raise.

    Args:
        messages: The full conversation messages array.
        spec: The target model's specification (context_window limit).
        threshold_pct: Fraction of context window at which to start truncating.

    Returns:
        A (possibly truncated) messages list.

    Raises:
        ValueError: If the payload cannot be reduced below the threshold.
    """
    max_tokens = spec.context_window
    threshold = int(max_tokens * threshold_pct)
    estimated = estimate_token_count(messages)

    if estimated <= threshold:
        logger.debug("[gateway] Token estimate %d within threshold %d/%d.", estimated, threshold, max_tokens)
        return messages

    logger.warning(
        "[gateway] Token estimate %d exceeds %d%% threshold (%d/%d). Truncating conversation.",
        estimated,
        int(threshold_pct * 100),
        threshold,
        max_tokens,
    )

    if len(messages) <= 2:
        # Only system prompt + current user message; can't truncate further
        raise ValueError(
            f"Cannot reduce payload below {estimate_token_count(messages)} tokens. "
            f"Model context window: {max_tokens}. Consider splitting the task."
        )

    # Core strategy: keep system prompt [0] and last message [-1]
    preserved = [messages[0], messages[-1]]
    preserved_count = estimate_token_count(preserved)

    # If even just the system prompt + last message exceeds threshold, fail
    if preserved_count > threshold:
        raise ValueError(
            f"System prompt + current message alone exceed the context threshold "
            f"({preserved_count} > {threshold}). Reduce the system prompt size or split the task."
        )

    # Build truncated list: system + most recent N messages that fit
    truncated = [messages[0]]
    available_budget = threshold - estimate_token_count(truncated) - estimate_token_count([messages[-1]])

    # Fill from the end (most recent first) excluding system[0] and last[-1]
    middle_messages = messages[1:-1]
    insertion_point = 1  # After system prompt

    for msg in reversed(middle_messages):
        msg_estimate = estimate_token_count([msg])
        if msg_estimate <= available_budget:
            truncated.insert(insertion_point, msg)
            available_budget -= msg_estimate
        else:
            break  # Can't fit more; oldest messages get dropped

    truncated.append(messages[-1])

    final_estimate = estimate_token_count(truncated)
    logger.info(
        "[gateway] Truncation complete. %d → %d messages, %d → ~%d tokens.",
        len(messages),
        len(truncated),
        estimated,
        final_estimate,
    )
    return truncated


# ---------------------------------------------------------------------------
# 11. Exponential Backoff with Jitter
# ---------------------------------------------------------------------------

async def retry_with_backoff(
    fn: Callable[..., Awaitable[LLMResponse]],
    *args: Any,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    **kwargs: Any,
) -> LLMResponse:
    """
    Execute an async LLM call with exponential backoff + random jitter.

    Handles HTTP 429 (rate limit), 5xx (server errors), and connection errors.
    After max_retries, re-raises the last exception.

    Backoff formula: min(max_delay, base_delay * 2^attempt) * (0.5 + random * 0.5)
    This gives a jitter range of 50%-100% of the exponential base.
    """
    last_exception: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                # Respect Retry-After header if present; otherwise use our backoff
                retry_after = exc.response.headers.get("Retry-After")
                if retry_after is not None:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = base_delay * (2 ** attempt)
                else:
                    delay = base_delay * (2 ** attempt)
                logger.warning("[gateway] Rate limited (429). Attempt %d/%d.", attempt + 1, max_retries + 1)
            elif status >= 500:
                delay = base_delay * (2 ** attempt)
                logger.warning("[gateway] Server error (%d). Attempt %d/%d.", status, attempt + 1, max_retries + 1)
            else:
                raise  # Non-retryable HTTP error (4xx except 429)
            last_exception = exc
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            delay = base_delay * (2 ** attempt)
            logger.warning("[gateway] Connection error. Attempt %d/%d. %s", attempt + 1, max_retries + 1, exc)
            last_exception = exc

        if attempt < max_retries:
            # Apply jitter: 50%-100% of computed delay
            jittered = delay * (0.5 + random.random() * 0.5)
            jittered = min(jittered, max_delay)
            logger.debug("[gateway] Backing off for %.2fs before retry.", jittered)
            await asyncio.sleep(jittered)

    raise last_exception  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 12. Gateway Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class GatewayConfig:
    """Runtime configuration for the LLM gateway, parsed from .harness_config.json.

    All model keys default to empty strings. Users must configure model routing
    via .harness_config.json. No default models are bundled.
    """
    planning_primary: str = ""
    planning_mode: str = "thinking_max"
    planning_fallback: str = ""
    patching_primary: str = ""
    patching_mode: str = "non_thinking"
    repair_primary: str = ""
    repair_fallback: str = ""
    repair_mode: str = "thinking"
    ollama_local_model: str = ""
    ollama_local_backup: str = ""
    force_local_only: bool = False
    hard_cap_usd: float = 2.00
    context_window_threshold_pct: float = 0.85
    max_retries: int = 5
    base_delay: float = 1.0


class Gateway:
    """
    Central orchestrator for model-agnostic LLM dispatching.

    Responsibilities:
        - Route calls to the correct provider based on NodeRole and config.
        - Enforce token budget (rejects calls when budget_remaining_usd <= 0).
        - Apply prefix caching anchor at messages[0].
        - Run pre-flight context window guardrail checks.
        - Aggregate token usage into the LangGraph state token_tracker.
        - Handle retry with exponential backoff.
    """

    def __init__(self, config: GatewayConfig):
        self.config = config
        # Provider cache: lazily instantiated per unique model_key
        self._providers: dict[str, BaseLLM] = {}

    async def _get_provider(self, model_key: str) -> BaseLLM:
        """Get or create a cached provider instance."""
        if model_key not in self._providers:
            self._providers[model_key] = create_provider(model_key)
        return self._providers[model_key]

    async def close(self) -> None:
        """Close all open provider HTTP clients."""
        for provider in self._providers.values():
            await provider.close()
        self._providers.clear()

    def select_model(self, role: NodeRole, force_local: bool = False) -> str:
        """
        Select the appropriate model for a given node role based on config.

        Args:
            role: The graph node making the request.
            force_local: If True (or config.force_local_only), use local Ollama only.

        Returns:
            The canonical model key to use.
        """
        if force_local or self.config.force_local_only:
            return f"ollama:{self.config.ollama_local_model}"

        if role == NodeRole.PLANNING:
            return self.config.planning_primary
        elif role == NodeRole.PATCHING:
            return self.config.patching_primary
        elif role == NodeRole.REPAIR:
            return self.config.repair_primary
        else:
            return self.config.patching_primary  # Default

    def should_use_thinking(self, role: NodeRole) -> bool:
        """Determine if thinking/reasoning mode should be enabled for this role."""
        if role == NodeRole.PLANNING:
            return "thinking" in self.config.planning_mode.lower()
        elif role == NodeRole.PATCHING:
            return "thinking" in self.config.patching_mode.lower()
        elif role == NodeRole.REPAIR:
            return "thinking" in self.config.repair_mode.lower()
        return False

    async def dispatch(
        self,
        *,
        messages: list[dict[str, Any]],
        role: NodeRole,
        budget_remaining_usd: float,
        force_local: bool = False,
        **llm_kwargs: Any,
    ) -> tuple[LLMResponse, float]:
        """
        Dispatch an LLM call with full guardrails.

        Args:
            messages: The conversation messages array.
            role: Which graph node is making the call.
            budget_remaining_usd: Current remaining budget. If <= 0, the call is refused.
            force_local: If True, force local Ollama inference.
            **llm_kwargs: Additional parameters passed to the provider's chat_completion.

        Returns:
            A tuple of (LLMResponse, new_budget_remaining_usd).

        Raises:
            RuntimeError: If the budget is exhausted.
        """
        # Financial guardrail
        if budget_remaining_usd <= 0.0:
            raise RuntimeError(
                f"[GUARDRAIL EXHAUSTED]: Active session hit the ${self.config.hard_cap_usd:.2f} threshold. "
                f"Budget remaining: ${budget_remaining_usd:.4f}"
            )

        # Select model + provider
        model_key = self.select_model(role, force_local=force_local)
        thinking = self.should_use_thinking(role)

        # If budget is low and not forcing local, fall back to ollama to preserve budget
        if budget_remaining_usd < 0.05 and not force_local and not self.config.force_local_only:
            logger.info(
                "[gateway] Budget low ($%.4f). Switching to local Ollama to preserve remaining budget.",
                budget_remaining_usd,
            )
            model_key = f"ollama:{self.config.ollama_local_model}"
            force_local = True
            thinking = False

        provider = await self._get_provider(model_key)
        spec = provider.spec

        # --- Redact secrets from messages before transmission ---
        try:
            from harness.redactor import redact_messages
            messages = redact_messages(messages)
        except ImportError:
            pass  # Redactor not installed — messages go out as-is

        # Anchor system prompt at messages[0] for prefix caching
        messages = ensure_prefix_cache_anchor(list(messages))

        # Pre-flight context window guardrail
        messages = await check_context_window(
            messages,
            spec,
            threshold_pct=self.config.context_window_threshold_pct,
        )

        # Execute with retry/backoff
        logger.info("[gateway] Dispatching to %s (role=%s, thinking=%s).", model_key, role.value, thinking)

        async def _call() -> LLMResponse:
            return await provider.chat_completion(
                messages=messages,
                thinking=thinking,
                **llm_kwargs,
            )

        response = await retry_with_backoff(
            _call,
            max_retries=self.config.max_retries,
            base_delay=self.config.base_delay,
        )

        # Deduct cost from budget
        cost = response.usage.cost_usd
        new_budget = budget_remaining_usd - cost

        logger.info(
            "[gateway] Response received. model=%s tokens_in=%d tokens_out=%d cache_hit=%d cost=$%.6f budget_left=$%.4f",
            response.model,
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.usage.cached_tokens,
            cost,
            new_budget,
        )

        return response, new_budget

    def aggregate_tokens(
        self,
        tracker: dict[str, Any],
        usage: TokenUsage,
    ) -> dict[str, Any]:
        """
        Merge token usage from a single LLM call into the cumulative tracker.

        Args:
            tracker: The current token_tracker dict from AgentState.
            usage: The TokenUsage from a single LLMResponse.

        Returns:
            Updated tracker dict.
        """
        tracker["total_input_tokens"] = tracker.get("total_input_tokens", 0) + usage.input_tokens
        tracker["total_output_tokens"] = tracker.get("total_output_tokens", 0) + usage.output_tokens
        tracker["total_cached_tokens"] = tracker.get("total_cached_tokens", 0) + usage.cached_tokens
        tracker["total_cost_usd"] = tracker.get("total_cost_usd", 0.0) + usage.cost_usd

        # Per-model breakdown
        per_model: dict[str, dict[str, Any]] = tracker.setdefault("per_model", {})
        model_key = f"{usage.model_name}"
        if model_key not in per_model:
            per_model[model_key] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "cost_usd": 0.0,
            }
        per_model[model_key]["input_tokens"] += usage.input_tokens
        per_model[model_key]["output_tokens"] += usage.output_tokens
        per_model[model_key]["cached_tokens"] += usage.cached_tokens
        per_model[model_key]["cost_usd"] += usage.cost_usd

        return tracker


# ---------------------------------------------------------------------------
# 13. Gateway Factory from Config
# ---------------------------------------------------------------------------

def create_gateway_from_config(config_dict: dict[str, Any]) -> Gateway:
    """
    Build a Gateway instance from a .harness_config.json dictionary.

    Also registers any models defined in the 'models' section of the config.

    Args:
        config_dict: Parsed JSON dict from .harness_config.json.

    Returns:
        Configured Gateway instance.
    """
    # Register models from the 'models' section
    register_models_from_config(config_dict)

    model_routing = config_dict.get("model_routing", {})
    token_budget = config_dict.get("token_budget", {})

    gateway_config = GatewayConfig(
        planning_primary=model_routing.get("planning_primary", ""),
        planning_mode=model_routing.get("planning_mode", "thinking_max"),
        planning_fallback=model_routing.get("planning_fallback", ""),
        patching_primary=model_routing.get("patching_primary", ""),
        patching_mode=model_routing.get("patching_mode", "non_thinking"),
        repair_primary=model_routing.get("repair_primary", ""),
        repair_fallback=model_routing.get("repair_fallback", ""),
        repair_mode=model_routing.get("repair_mode", "thinking"),
        ollama_local_model=model_routing.get("ollama_local_model", ""),
        ollama_local_backup=model_routing.get("ollama_local_backup", ""),
        force_local_only=model_routing.get("force_local_only", False),
        hard_cap_usd=token_budget.get("hard_cap_usd", 2.00),
        context_window_threshold_pct=token_budget.get("context_window_threshold_pct", 0.85),
    )
    return Gateway(gateway_config)
