"""Form-schema derivation from the strict config validator.

The dashboard's config editor is form-based. Hand-curating a form per
config section would drift the moment a new key landed in
``harness/cli.py``'s validator tables, so we **derive** the form
schema from the same source the validator uses:

- ``_KNOWN_TOP_LEVEL_KEYS`` — the section names.
- ``_KNOWN_NESTED_KEYS[section]`` — the per-section field names.
- ``_TYPE_SCHEMA[\"section.field\"]`` — the runtime type tuple.

A round-trip test asserts every nested key in ``_TYPE_SCHEMA`` is
renderable + persistable through this layer; if someone lands a new
key without a type entry the form refuses to render the field (and
the round-trip test fails CI), so the validator stays the source of
truth.

The HTML renderer in :mod:`harness.dashboard` consumes the descriptors
this module produces. No HTML lives here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Form field descriptor
# ---------------------------------------------------------------------------

FORM_KIND_CHECKBOX = "checkbox"   # bool
FORM_KIND_NUMBER_INT = "number_int"
FORM_KIND_NUMBER_FLOAT = "number_float"
FORM_KIND_TEXT = "text"
FORM_KIND_TEXTAREA = "textarea"
FORM_KIND_JSON_LIST = "json_list"
FORM_KIND_JSON_DICT = "json_dict"
FORM_KIND_SELECT = "select"       # str with a finite choice set


# Enum tables: dotted-key → tuple of valid string values. When a field's
# dotted key is in this map, the form renders a <select> and the parser
# rejects any value not in the list. Seeded from cli.py's existing enum
# frozensets — extend here as new bounded-choice fields land.
def _field_choices() -> dict[str, tuple[str, ...]]:
    # Late import — cli.py imports web_forms transitively, so deferring
    # avoids a circular import at module load.
    from harness.cli import _VALID_SANDBOX_BACKENDS, _VALID_SELECTION_STRATEGIES
    return {
        "sandbox.backend": tuple(sorted(_VALID_SANDBOX_BACKENDS)),
        "speculative.selection_strategy": tuple(sorted(_VALID_SELECTION_STRATEGIES)),
    }


# Operator-facing descriptions per dotted key. The form's middle column
# ("Meaning") reads from this map. Empty string when a key isn't listed —
# the renderer falls back to "no description".
_FIELD_DESCRIPTIONS: dict[str, str] = {
    # Top-level scalars.
    "build_command": "Shell command the harness runs after applying patches to verify the build.",
    "allow_network": "Whether the sandbox may reach the network. Off by default for security.",
    "product_spec_dir": "Folder name (relative to the workspace) containing the product spec files (.txt / .md / .pdf) the planner reads.",
    "change_requests_dir": "Folder name (relative to the workspace) holding change-request files (.txt / .md / .pdf) for brownfield runs.",
    # Sandbox.
    "sandbox.backend": "Sandbox engine. 'auto' picks the best available on this host.",
    "sandbox.docker_image": "Docker image used when backend is 'docker'.",
    "sandbox.docker_memory_limit": "Memory limit for the docker sandbox (e.g. '2g').",
    "sandbox.docker_cpu_limit": "CPU limit for the docker sandbox (number of cores, may be fractional).",
    # Token budget.
    "token_budget.hard_cap_usd": "Hard ceiling on cumulative LLM spend per session. Exits when crossed.",
    "token_budget.context_window_threshold_pct": "Trigger speculative compaction when context fills past this %.",
    # Logging.
    "logging.log_dir": "Directory where per-session JSONL logs are written.",
    "logging.level": "Python logging level (DEBUG, INFO, WARNING, ERROR).",
    # Schedule.
    "schedule.enabled": "Whether the cron-driven scheduled-job daemon is on.",
    "schedule.tick_seconds": "How often the daemon checks for due jobs.",
    "schedule.harness_binary": "Path to the harness CLI (used to spawn scheduled runs).",
    # Dashboard.
    "dashboard.enabled": "Whether the web UI is allowed to start (the subcommand itself overrides this).",
    "dashboard.host": "Bind host for the web UI. Default 127.0.0.1 for localhost-only.",
    "dashboard.port": "Bind port for the web UI.",
    "dashboard.token_env": "Env var name holding the bearer token. Empty disables auth.",
    "dashboard.writes_enabled": "Allow the editing UI + Run-from-web. Off = read-only.",
    "dashboard.docs_dir": "Folder of .md / .txt docs surfaced under View Documents.",
    "dashboard.carbon_css_url": "URL to the Carbon Design System CSS. Override for air-gapped deploys.",
    # Speculative.
    "speculative.selection_strategy": "How parallel speculative attempts are picked.",
    # Repo index.
    "repo_index.enabled": "Inject semantic retrieval results into the planner context.",
    "repo_index.backend": "Retrieval backend: 'tfidf' (default) or 'openai_embeddings'.",
    "repo_index.top_k": "Number of chunks injected into the planner context.",
    # Web tools.
    "web_tools.enabled": "Register web_fetch / web_search skills with the gateway.",
    "web_tools.max_bytes": "Max bytes returned by a single web_fetch call.",
    "web_tools.timeout_seconds": "Timeout for web_fetch and web_search calls.",
    # Persistence.
    "persistence.db_path": "Path to the LangGraph checkpoint SQLite database.",
}


@dataclass
class FormField:
    """One renderable field in a config section.

    ``kind`` is one of the ``FORM_KIND_*`` constants. ``type_tuple`` is
    the validator's runtime-type tuple (preserved so the form's POST
    handler can route the parsed value through the same gate the
    validator uses).
    """

    section: str
    name: str
    kind: str
    type_tuple: tuple[type, ...]
    current_value: Any = None
    required: bool = False
    secret: bool = False  # render as <input type=password>; never echo on errors
    placeholder: str = ""
    choices: Optional[tuple[str, ...]] = None  # for FORM_KIND_SELECT
    description: str = ""

    @property
    def dotted_key(self) -> str:
        return f"{self.section}.{self.name}" if self.section else self.name


@dataclass
class FormSection:
    """All renderable fields in one section.

    ``section`` is the top-level config key. ``fields`` is in
    sorted-name order. Sections whose every type entry is missing fall
    out as empty, which is fine — the renderer skips them.
    """

    section: str
    fields: list[FormField] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. Type → form kind mapping
# ---------------------------------------------------------------------------

def kind_for_type_tuple(type_tuple: tuple[type, ...]) -> str:
    """Pick the render kind from the validator's type tuple.

    Order of preference (longest-match first so ``(int, float)`` picks
    float, not int):

    - ``(bool,)`` → checkbox
    - ``(int, float)`` or ``(float, ...)`` → number with step=any
    - ``(int,)`` → number with step=1
    - ``(list,)`` → JSON list textarea
    - ``(dict,)`` → JSON dict textarea
    - ``(str,)`` → text input
    - anything mixed or unknown → text input as the safest fallback
    """
    if not type_tuple:
        return FORM_KIND_TEXT
    s = set(type_tuple)
    if bool in s:
        return FORM_KIND_CHECKBOX
    if float in s:
        return FORM_KIND_NUMBER_FLOAT
    if int in s and float not in s:
        return FORM_KIND_NUMBER_INT
    if list in s:
        return FORM_KIND_JSON_LIST
    if dict in s:
        return FORM_KIND_JSON_DICT
    return FORM_KIND_TEXT


# ---------------------------------------------------------------------------
# 3. Building descriptors from the live validator tables
# ---------------------------------------------------------------------------

def build_section(
    section: str,
    *,
    current_config: Optional[dict[str, Any]] = None,
) -> FormSection:
    """Build a :class:`FormSection` for one top-level config section.

    Pulls field names from :data:`harness.cli._KNOWN_NESTED_KEYS` and
    types from :data:`harness.cli._TYPE_SCHEMA`. Fields without a type
    entry are quietly dropped (the round-trip test guards against this).
    """
    from harness.cli import _KNOWN_NESTED_KEYS, _TYPE_SCHEMA
    field_names = sorted(_KNOWN_NESTED_KEYS.get(section, frozenset()))
    out = FormSection(section=section)
    section_data = (current_config or {}).get(section) or {}
    choices_map = _field_choices()
    for name in field_names:
        dotted = f"{section}.{name}"
        type_tuple = _TYPE_SCHEMA.get(dotted)
        if type_tuple is None:
            logger.debug("[web_forms] no type entry for %s; skipping", dotted)
            continue
        choices = choices_map.get(dotted)
        kind = FORM_KIND_SELECT if choices else kind_for_type_tuple(type_tuple)
        out.fields.append(FormField(
            section=section, name=name,
            kind=kind,
            type_tuple=type_tuple,
            current_value=section_data.get(name),
            choices=choices,
            description=_FIELD_DESCRIPTIONS.get(dotted, ""),
        ))
    return out


def all_sections(
    *, current_config: Optional[dict[str, Any]] = None,
) -> list[FormSection]:
    """Build form schemas for every known top-level section. Sections
    without any renderable fields are still returned (empty) so the UI
    can render a placeholder.
    """
    from harness.cli import _KNOWN_TOP_LEVEL_KEYS, _KNOWN_NESTED_KEYS
    out: list[FormSection] = []
    for section in sorted(_KNOWN_TOP_LEVEL_KEYS):
        # Top-level scalar keys (e.g. "build_command") aren't in
        # _KNOWN_NESTED_KEYS; render them as a single-field section
        # carrying the section name as the field name.
        if section not in _KNOWN_NESTED_KEYS:
            from harness.cli import _TYPE_SCHEMA
            type_tuple = _TYPE_SCHEMA.get(section)
            if type_tuple is None:
                # Scalar section without a typed entry (e.g. "models",
                # "model_routing" — render via per-section editors,
                # not the generic form). Skip from the generic editor.
                out.append(FormSection(section=section, fields=[]))
                continue
            section_data = (current_config or {})
            out.append(FormSection(
                section=section,
                fields=[FormField(
                    section="", name=section,
                    kind=kind_for_type_tuple(type_tuple),
                    type_tuple=type_tuple,
                    current_value=section_data.get(section),
                    description=_FIELD_DESCRIPTIONS.get(section, ""),
                )],
            ))
            continue
        out.append(build_section(section, current_config=current_config))
    return out


# ---------------------------------------------------------------------------
# 4. Parsing form POST data back into typed Python values
# ---------------------------------------------------------------------------

class FormParseError(ValueError):
    """Raised when a form field value can't be parsed into its declared
    type. Carries the offending dotted key + the operator-facing error
    message so the renderer can show a per-field error."""

    def __init__(self, dotted_key: str, message: str):
        super().__init__(f"{dotted_key}: {message}")
        self.dotted_key = dotted_key
        self.message = message


def parse_value(field_: FormField, raw: Any) -> Any:
    """Parse a single field's raw POST value into its declared type.

    HTML form values arrive as strings (or lists for multi-valued
    fields); checkboxes are present-or-absent. We coerce to the
    validator's expected type and raise :class:`FormParseError` on
    failure so the renderer can surface a per-field error.
    """
    kind = field_.kind
    if kind == FORM_KIND_SELECT:
        choice = "" if raw is None else str(raw).strip()
        if not choice:
            raise FormParseError(field_.dotted_key, "value required (pick one)")
        choices = field_.choices or ()
        if choice not in choices:
            raise FormParseError(
                field_.dotted_key,
                f"value {choice!r} not in {list(choices)}",
            )
        return choice
    if kind == FORM_KIND_CHECKBOX:
        if isinstance(raw, bool):
            return raw
        if raw is None or raw == "":
            return False
        if isinstance(raw, str):
            return raw.lower() in ("1", "true", "on", "yes")
        return bool(raw)
    if kind == FORM_KIND_NUMBER_INT:
        if raw is None or raw == "":
            raise FormParseError(field_.dotted_key, "value required (integer)")
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise FormParseError(field_.dotted_key, f"not a valid integer: {exc}") from exc
    if kind == FORM_KIND_NUMBER_FLOAT:
        if raw is None or raw == "":
            raise FormParseError(field_.dotted_key, "value required (number)")
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise FormParseError(field_.dotted_key, f"not a valid number: {exc}") from exc
    if kind == FORM_KIND_JSON_LIST:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return []
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise FormParseError(field_.dotted_key, f"not valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise FormParseError(field_.dotted_key, "JSON must be a list")
        return parsed
    if kind == FORM_KIND_JSON_DICT:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return {}
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise FormParseError(field_.dotted_key, f"not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise FormParseError(field_.dotted_key, "JSON must be an object")
        return parsed
    # text / unknown — pass through stringified.
    if raw is None:
        return ""
    return str(raw)


def parse_section_post(
    section: FormSection, post_data: dict[str, Any],
) -> tuple[dict[str, Any], list[FormParseError]]:
    """Parse a posted form payload back into a section dict.

    ``post_data`` is a mapping of dotted-key → raw form value (string
    or list, as multipart/x-www-form-urlencoded delivers). Missing
    checkboxes are interpreted as ``False`` (HTML omits absent
    checkboxes from the POST body).

    Returns ``(section_dict, errors)`` so the caller can re-render the
    form with per-field errors when validation fails. The ``section_dict``
    is **always** present even when ``errors`` is non-empty so the
    operator's other typed values aren't lost.
    """
    out: dict[str, Any] = {}
    errors: list[FormParseError] = []
    for f in section.fields:
        raw = post_data.get(f.dotted_key, None)
        # Lists from form parsing arrive as the first/last; pick last
        # to honour "value last write wins".
        if isinstance(raw, list):
            raw = raw[-1] if raw else None
        try:
            value = parse_value(f, raw)
        except FormParseError as exc:
            errors.append(exc)
            continue
        out[f.name] = value
    return out, errors


# ---------------------------------------------------------------------------
# 5. Coverage check — round-trip validator vs. form schema
# ---------------------------------------------------------------------------

def renderable_dotted_keys() -> set[str]:
    """Every dotted key the form schema can render. The round-trip test
    asserts this equals (or is a near-equal of) the validator's
    ``_TYPE_SCHEMA`` keys. New config keys land in both lists or fail
    the test."""
    sections = all_sections(current_config=None)
    keys: set[str] = set()
    for s in sections:
        for f in s.fields:
            keys.add(f.dotted_key)
    return keys


# ---------------------------------------------------------------------------
# 6. Run-harness CLI flag schema (per-flag inputs on the Run page)
# ---------------------------------------------------------------------------

# Yes/No select kind — operator-friendly form rendering for boolean CLI
# flags. The form layer maps "yes" → emit the flag, "no" → omit; this
# is more discoverable than a single text box where operators have to
# remember --allow-network vs --allow_network.
FORM_KIND_YES_NO = "yes_no"


@dataclass
class RunFlag:
    """One CLI flag the operator can toggle from the Run Harness page.

    ``kind`` is one of the ``FORM_KIND_*`` constants. ``flag`` is the
    canonical long-form (e.g. ``--allow-network``); SELECT flags emit
    ``--flag=value`` for the bool-choice flags such as ``--git false``.
    ``yes_emits_flag`` controls how ``FORM_KIND_YES_NO`` collapses to an
    argv list: True means "yes" emits the flag and "no" omits it
    (store_true semantics); False is the opposite.

    ``subcommands`` is the tuple of teane subcommands this flag applies
    to (e.g. ``("build", "patch")``). The Run-page renderer only shows
    flags whose tuple contains the currently selected subcommand, and
    ``build_subcommand_argv_from_form`` only emits flags for the
    matching subcommand. Empty tuple = applies to every run-like
    subcommand (audit excluded — audit takes only --workspace).
    """

    name: str                 # form field id ("allow_network")
    label: str                # human-facing label ("Allow network")
    description: str          # operator-facing helper text
    kind: str                 # FORM_KIND_*
    flag: str = ""            # e.g. "--allow-network"; for SELECT/TEXT/NUMBER, emitted as `<flag> <value>`
    default: Any = None       # default form value
    yes_emits_flag: bool = True   # YES_NO: "yes" → emit flag, "no" → omit
    choices: tuple[str, ...] = ()  # FORM_KIND_SELECT
    min_value: Optional[int] = None  # FORM_KIND_NUMBER_INT bounds
    max_value: Optional[int] = None
    subcommands: tuple[str, ...] = ()  # empty = all non-audit subcommands

    @property
    def field_id(self) -> str:
        return f"flag.{self.name}"

    def applies_to(self, subcommand: str) -> bool:
        if not self.subcommands:
            # Empty tuple = all non-audit subcommands.
            return subcommand in ("build", "patch", "deploy", "test")
        return subcommand in self.subcommands


# Operator-facing schema of every flag the Run page surfaces. Workspace +
# prompt have dedicated inputs at the top of the form; the per-flag
# inputs below mirror the build/patch/deploy/test argparse surfaces in
# ``harness.cli`` (the legacy ``run`` subparser was removed). Operators
# still reach text-only flags (verbose, force-lock, session-id, etc.)
# from the terminal.
#
# Sentinel ``(default)`` value on tri-state SELECT flags means "use the
# CLI default" — the form skips emission so the CLI applies its own
# fallback chain (e.g. config.json hitl.requirement, then true).
_TRI_DEFAULT = "(default)"
_TRI_CHOICES = (_TRI_DEFAULT, "false", "true")

_RUN_FLAGS: tuple[RunFlag, ...] = (
    # --- shared by build / patch / deploy / test ---
    RunFlag(
        name="git",
        label="Git mode",
        description="Enable GitGuardian stash/patch-branch/rollback for the workspace. True requires a git workspace; false skips every git-aware step. Defaults to false. Security scanners still run either way.",
        kind=FORM_KIND_SELECT,
        flag="--git",
        default="false",
        choices=("false", "true"),
        subcommands=(),  # all run-likes
    ),
    # --- build only ---
    RunFlag(
        name="spec_discovery",
        label="Spec discovery",
        description="When true, run BOTH the requirements and architecture discovery interviews before code generation. Recommended for greenfield projects. Defaults to false.",
        kind=FORM_KIND_SELECT,
        flag="--spec-discovery",
        default="false",
        choices=("false", "true"),
        subcommands=("build", "patch"),
    ),
    RunFlag(
        name="cd_discovery",
        label="Deployment discovery",
        description="When true, run deployment discovery and write DEPLOYMENT_BLUEPRINT.md. Build/patch only writes the doc — `teane deploy` synthesises artifacts and brings the dev container up. Defaults to false.",
        kind=FORM_KIND_SELECT,
        flag="--cd-discovery",
        default="false",
        choices=("false", "true"),
        subcommands=("build", "patch", "deploy"),
    ),
    RunFlag(
        name="agile",
        label="Agile mode",
        description="Engage Agile-style story decomposition + per-story TDD. Build: explicit choice (default false). Patch: '(default)' = auto-detect from .teane/state.db (non-empty → agile). Per-knob tuning (batch_size, commit_on_story, repair_cap) lives in config.json's agile_defaults block.",
        kind=FORM_KIND_SELECT,
        flag="--agile",
        default=_TRI_DEFAULT,
        choices=_TRI_CHOICES,
        subcommands=("build", "patch"),
    ),
    # --- patch only ---
    RunFlag(
        name="generate_specs",
        label="Generate specs",
        description="Reverse-engineer SPEC_REQUIREMENTS.md / SPEC_ARCHITECTURE.md from the existing codebase. '(default)' = auto (generate only when both spec files are missing). 'true' = always regenerate. 'false' = error if specs missing.",
        kind=FORM_KIND_SELECT,
        flag="--generate-specs",
        default=_TRI_DEFAULT,
        choices=_TRI_CHOICES,
        subcommands=("patch",),
    ),
    RunFlag(
        name="install_doc",
        label="Update INSTALLATION.md",
        description="Update INSTALLATION.md at the end of a successful patch. '(default)' = auto (agile patches enable; non-agile patches skip). Override explicitly with true/false.",
        kind=FORM_KIND_SELECT,
        flag="--install-doc",
        default=_TRI_DEFAULT,
        choices=_TRI_CHOICES,
        subcommands=("patch",),
    ),
    # --- HITL gates (build + patch share four; deploy has its own) ---
    RunFlag(
        name="hitl_requirement",
        label="HITL: requirements gate",
        description="When true, prompt the operator at the REQUIREMENTS gate. '(default)' = use config.json hitl.requirement, then true.",
        kind=FORM_KIND_SELECT,
        flag="--hitl-requirement",
        default=_TRI_DEFAULT,
        choices=_TRI_CHOICES,
        subcommands=("build", "patch"),
    ),
    RunFlag(
        name="hitl_architecture",
        label="HITL: architecture gate",
        description="When true, prompt the operator at the ARCHITECTURE gatekeeper. '(default)' = use config.json hitl.architecture, then true.",
        kind=FORM_KIND_SELECT,
        flag="--hitl-architecture",
        default=_TRI_DEFAULT,
        choices=_TRI_CHOICES,
        subcommands=("build", "patch"),
    ),
    RunFlag(
        name="hitl_repair",
        label="HITL: repair-loop menu",
        description="When true, fire the repair-loop HITL menu when iteration limits trip. '(default)' = use config.json hitl.repair, then true.",
        kind=FORM_KIND_SELECT,
        flag="--hitl-repair",
        default=_TRI_DEFAULT,
        choices=_TRI_CHOICES,
        subcommands=("build", "patch"),
    ),
    RunFlag(
        name="hitl_layout_divergence",
        label="HITL: layout divergence",
        description="When true, prompt when the on-disk layout drifts from SPEC_ARCHITECTURE.md's workspace_layout. '(default)' = use config.json hitl.layout_divergence.",
        kind=FORM_KIND_SELECT,
        flag="--hitl-layout-divergence",
        default=_TRI_DEFAULT,
        choices=_TRI_CHOICES,
        subcommands=("build", "patch"),
    ),
    RunFlag(
        name="hitl_deployment",
        label="HITL: deployment gate",
        description="When true, prompt the operator at the DEPLOYMENT gate before the dev deploy fires. '(default)' = use config.json hitl.deployment, then true.",
        kind=FORM_KIND_SELECT,
        flag="--hitl-deployment",
        default=_TRI_DEFAULT,
        choices=_TRI_CHOICES,
        subcommands=("deploy",),
    ),
    # --- test only ---
    RunFlag(
        name="scope",
        label="Test scope",
        description="Which scenarios to run. 'touched' (default) — only scenarios whose source spec touches a service in the last deploy's CR attribution. 'full' — run every scenario in tests/e2e/.",
        kind=FORM_KIND_SELECT,
        flag="--scope",
        default="touched",
        choices=("touched", "full"),
        subcommands=("test",),
    ),
    RunFlag(
        name="retries",
        label="Per-scenario retries",
        description="Playwright per-scenario retry count for flake suppression. Defaults to 2; set 0 to disable.",
        kind=FORM_KIND_NUMBER_INT,
        flag="--retries",
        default=2,
        min_value=0,
        max_value=10,
        subcommands=("test",),
    ),
    RunFlag(
        name="no_cleanup",
        label="Keep synthetic data",
        description="Skip teardown of generated synthetic data after the run. Useful when debugging a failed scenario against the live compose stack.",
        kind=FORM_KIND_YES_NO,
        flag="--no-cleanup",
        default="no",
        yes_emits_flag=True,
        subcommands=("test",),
    ),
)


def run_flags() -> tuple[RunFlag, ...]:
    """Operator-facing CLI flag schema — every flag the Run page knows."""
    return _RUN_FLAGS


def run_flags_for(subcommand: str) -> tuple[RunFlag, ...]:
    """The subset of flags applicable to ``subcommand``.

    Audit takes only ``--workspace`` (per ``cli.py:9080``), so this
    returns an empty tuple when ``subcommand == "audit"``.
    """
    if subcommand == "audit":
        return ()
    return tuple(f for f in _RUN_FLAGS if f.applies_to(subcommand))


def _flag_value_from_form(
    flag: RunFlag, post_data: dict[str, Any],
) -> str:
    raw = post_data.get(flag.field_id, None)
    if isinstance(raw, list):
        raw = raw[-1] if raw else None
    if raw is None:
        return ""
    return str(raw).strip()


def build_subcommand_argv_from_form(
    post_data: dict[str, Any],
    *,
    subcommand: str,
) -> tuple[list[str], list[str]]:
    """Translate a POSTed Run-page form into ``(argv, errors)`` for the
    given ``subcommand``.

    Only flags whose ``subcommands`` tuple contains ``subcommand`` are
    consulted; anything outside that subset is ignored even if present
    in the POST body (operators can save a Build preset and load it on
    the Patch page without the build-only ``--spec-discovery`` flag
    leaking through).

    Returns the same shape as the legacy ``build_run_argv_from_form``:
    a list of CLI tokens and a list of per-flag operator-facing error
    strings suitable for re-rendering the form.
    """
    if subcommand == "audit":
        # Audit accepts only --workspace; the spawner emits that itself.
        # Nothing in the flag schema applies.
        return [], []

    argv: list[str] = []
    errors: list[str] = []
    for f in _RUN_FLAGS:
        if not f.applies_to(subcommand):
            continue
        value = _flag_value_from_form(f, post_data)

        if f.kind == FORM_KIND_TEXT:
            if not value:
                continue
            argv.extend([f.flag, value])
            continue
        if f.kind == FORM_KIND_YES_NO:
            picked = value.lower() if value else str(f.default).lower()
            if picked not in ("yes", "no"):
                errors.append(f"{f.name}: must be yes or no, got {value!r}")
                continue
            if (picked == "yes") == f.yes_emits_flag:
                argv.append(f.flag)
            continue
        if f.kind == FORM_KIND_SELECT:
            picked = value or str(f.default)
            if picked not in f.choices:
                errors.append(
                    f"{f.name}: value {picked!r} not in {list(f.choices)}"
                )
                continue
            # Skip when the operator hasn't moved off the form default —
            # the CLI applies the same default (or, for tri-state flags,
            # the form default sentinel _TRI_DEFAULT means "let the CLI
            # decide" so we deliberately omit the flag).
            if picked == str(f.default):
                continue
            argv.append(f"{f.flag}={picked}")
            continue
        if f.kind == FORM_KIND_NUMBER_INT:
            if not value:
                continue
            try:
                num = int(value)
            except ValueError:
                errors.append(f"{f.name}: not a valid integer ({value!r})")
                continue
            if f.min_value is not None and num < f.min_value:
                errors.append(f"{f.name}: must be >= {f.min_value}")
                continue
            if f.max_value is not None and num > f.max_value:
                errors.append(f"{f.name}: must be <= {f.max_value}")
                continue
            # Skip emission when the operator hasn't moved off the
            # default — the CLI applies the same number itself.
            if num == f.default:
                continue
            argv.extend([f.flag, str(num)])
            continue
        # Unknown kind — skip silently.
    return argv, errors


def build_run_argv_from_form(
    post_data: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Build-only alias of :func:`build_subcommand_argv_from_form`.

    Retained for the legacy ``/run/now`` POST handler whose form maps
    to ``teane build``. New per-subcommand routes call
    :func:`build_subcommand_argv_from_form` directly with the right
    ``subcommand``.
    """
    return build_subcommand_argv_from_form(post_data, subcommand="build")


# ---------------------------------------------------------------------------
# 7. Configure Harness page — semantic grouping of config sections
# ---------------------------------------------------------------------------

# Map every top-level config section to a human-facing group. The
# Configure Harness page renders one collapsible group per entry; the
# accordion of section editors sits inside the group body. When a new
# top-level section lands in :data:`harness.cli._KNOWN_TOP_LEVEL_KEYS`,
# add it to one of these groups so it's discoverable in the UI.
#
# Order in this tuple is the render order. Sections within each group
# stay in alphabetical order (consistent with ``all_sections``).
_CONFIG_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "general",
        "General",
        (
            "build_command", "allow_network",
            "product_spec_dir", "change_requests_dir", "change_requests",
            "patcher", "compiler", "languages", "core_languages",
        ),
    ),
    (
        "agile",
        "Agile",
        ("agile", "agile_defaults"),
    ),
    (
        "llm_registry",
        "LLM Registry",
        ("models",),
    ),
    (
        "llm_routing",
        "LLM Routing",
        ("model_routing", "llm_dispatch"),
    ),
    (
        "sandbox_security",
        "Sandbox & Security",
        ("sandbox", "security", "redaction"),
    ),
    (
        "budget_throttling",
        "Budget & Throttling",
        ("token_budget", "node_throttle", "metrics"),
    ),
    (
        "logging_debug",
        "Logging & Debug",
        ("logging", "debug"),
    ),
    (
        "skills_tools",
        "Skills & Tools",
        ("skills", "web_tools", "mcp"),
    ),
    (
        "patching_speculation",
        "Patching & Speculation",
        ("speculative", "impact", "lintgate", "test_generation"),
    ),
    (
        "storage_memory",
        "Storage & Memory",
        ("persistence", "memory", "repo_index"),
    ),
    (
        "deployment",
        "Deployment",
        ("deployment", "deployment_defaults"),
    ),
    (
        "scheduling",
        "Scheduling",
        ("schedule",),
    ),
    (
        "dashboard",
        "Harness Web",
        ("dashboard",),
    ),
    (
        "github",
        "GitHub",
        ("github",),
    ),
)


@dataclass
class ConfigGroup:
    """One collapsible group of related config sections on the Configure
    Harness page.

    ``sections`` is in the order ``_CONFIG_GROUPS`` declares; the renderer
    iterates that order directly.
    """

    slug: str
    title: str
    sections: list[FormSection] = field(default_factory=list)


def grouped_sections(
    *, current_config: Optional[dict[str, Any]] = None,
) -> list[ConfigGroup]:
    """Build the grouped form schema the Configure Harness page renders.

    Every section in :func:`all_sections` lands in exactly one group;
    sections not yet listed in :data:`_CONFIG_GROUPS` fall into a
    catch-all "Other" group so they remain reachable. The catch-all is a
    safety net — new sections should be moved into a semantic group as
    soon as they land, not left in Other.
    """
    sections = all_sections(current_config=current_config)
    by_name = {s.section: s for s in sections}
    groups: list[ConfigGroup] = []
    placed: set[str] = set()
    for slug, title, section_names in _CONFIG_GROUPS:
        group = ConfigGroup(slug=slug, title=title)
        for name in section_names:
            sec = by_name.get(name)
            if sec is None:
                continue
            group.sections.append(sec)
            placed.add(name)
        groups.append(group)
    unplaced = [s for s in sections if s.section not in placed]
    if unplaced:
        groups.append(ConfigGroup(slug="other", title="Other", sections=unplaced))
    return groups
