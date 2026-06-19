"""Tests for harness/architecture_inventory.py:parse_layout.

The architecture node now emits a `workspace_layout` JSON block at the
end of SPEC_ARCHITECTURE.md. The patcher allowlist is derived from this
block — see harness/graph.py:_spec_driven_allowlist. These tests cover
the parser end-to-end including the inventory-derivation fallback that
keeps the system backwards-compatible with specs written before the
layout contract existed.
"""

from __future__ import annotations

from harness.architecture_inventory import (
    LayoutRoot,
    parse_layout,
)


def _spec(*blocks: str, prelude: str = "# SPEC_ARCHITECTURE.md\n\nIntro text.\n\n") -> str:
    """Glue together a spec document with arbitrary fenced blocks."""
    return prelude + "\n\n".join(blocks) + "\n"


_INVENTORY_BLOCK = """\
```json
{
  "files": [
    {"path": "client/src/App.jsx", "purpose": "root component", "kind": "jsx"},
    {"path": "client/src/Login.jsx", "purpose": "login page", "kind": "jsx"},
    {"path": "server/src/index.js", "purpose": "express entry", "kind": "js"},
    {"path": "server/src/routes.js", "purpose": "api routes", "kind": "js"},
    {"path": "package.json", "purpose": "root manifest", "kind": "json"}
  ]
}
```"""

_LAYOUT_BLOCK = """\
```json
{
  "workspace_layout": {
    "roots": [
      {"path": "client", "purpose": "React frontend", "stack": "react"},
      {"path": "server", "purpose": "Express API", "stack": "express"}
    ],
    "test_placement": "co-located",
    "root_files": ["package.json", "tsconfig.json", "vite.config.ts"]
  }
}
```"""


class TestParseLayoutSuccess:
    """The happy path — a spec with both inventory and layout blocks."""

    def test_extracts_roots(self):
        result = parse_layout(_spec(_INVENTORY_BLOCK, _LAYOUT_BLOCK))
        assert result.ok
        assert result.has_layout
        assert [r.path for r in result.roots] == ["client", "server"]
        assert result.roots[0].purpose == "React frontend"
        assert result.roots[0].stack == "react"
        assert result.roots[1].purpose == "Express API"
        assert result.roots[1].stack == "express"
        assert result.derived_from_inventory is False

    def test_extracts_test_placement(self):
        result = parse_layout(_spec(_LAYOUT_BLOCK))
        assert result.test_placement == "co-located"

    def test_extracts_root_files(self):
        result = parse_layout(_spec(_LAYOUT_BLOCK))
        assert result.root_files == ["package.json", "tsconfig.json", "vite.config.ts"]

    def test_block_can_be_before_inventory(self):
        # Order shouldn't matter — parse_layout looks for workspace_layout
        # in every fenced block.
        result = parse_layout(_spec(_LAYOUT_BLOCK, _INVENTORY_BLOCK))
        assert result.has_layout
        assert [r.path for r in result.roots] == ["client", "server"]

    def test_dedups_repeated_roots(self):
        block = """\
```json
{"workspace_layout": {"roots": [
    {"path": "src"},
    {"path": "src", "purpose": "duplicate"}
]}}
```"""
        result = parse_layout(_spec(block))
        assert result.has_layout
        assert [r.path for r in result.roots] == ["src"]

    def test_normalizes_paths(self):
        block = """\
```json
{"workspace_layout": {"roots": [
    {"path": "./client/"},
    {"path": " server "}
]}}
```"""
        result = parse_layout(_spec(block))
        assert [r.path for r in result.roots] == ["client", "server"]


class TestParseLayoutValidation:
    """Tolerant on missing fields, strict on shape errors that would make
    the allowlist silently wrong."""

    def test_empty_document_returns_error(self):
        result = parse_layout("")
        assert not result.ok
        assert not result.has_layout
        assert "empty" in (result.error or "").lower()

    def test_no_layout_block_and_no_inventory_returns_error(self):
        plain = "# SPEC_ARCHITECTURE.md\n\nJust prose, no fenced blocks.\n"
        result = parse_layout(plain)
        assert not result.ok
        assert "workspace_layout" in (result.error or "")

    def test_nested_path_rejected(self):
        # Roots must be a single top-level directory name, not a nested path.
        block = """\
```json
{"workspace_layout": {"roots": [{"path": "client/src"}]}}
```"""
        result = parse_layout(_spec(block))
        assert not result.ok
        assert "single-segment" in (result.error or "")

    def test_missing_path_rejected(self):
        block = """\
```json
{"workspace_layout": {"roots": [{"purpose": "no path here"}]}}
```"""
        result = parse_layout(_spec(block))
        assert not result.ok

    def test_invalid_test_placement_dropped(self):
        block = """\
```json
{"workspace_layout": {
  "roots": [{"path": "src"}],
  "test_placement": "scattered"
}}
```"""
        result = parse_layout(_spec(block))
        assert result.has_layout      # tolerant — keep the rest
        assert result.test_placement == ""

    def test_roots_not_list_rejected(self):
        block = """\
```json
{"workspace_layout": {"roots": "src"}}
```"""
        result = parse_layout(_spec(block))
        assert not result.ok

    def test_workspace_layout_not_object_rejected(self):
        block = """\
```json
{"workspace_layout": ["client", "server"]}
```"""
        result = parse_layout(_spec(block))
        assert not result.ok


class TestParseLayoutInventoryFallback:
    """When a spec predates the layout contract, derive roots from the
    file inventory's top-level path components so backwards-compat is
    automatic."""

    def test_derives_roots_from_inventory_when_layout_absent(self):
        # Inventory is the SAME _INVENTORY_BLOCK as the success tests
        # use — but with NO workspace_layout block alongside.
        result = parse_layout(_spec(_INVENTORY_BLOCK))
        assert result.ok
        assert result.has_layout
        assert result.derived_from_inventory is True
        assert sorted(r.path for r in result.roots) == ["client", "server"]

    def test_derived_roots_have_no_metadata(self):
        result = parse_layout(_spec(_INVENTORY_BLOCK))
        for r in result.roots:
            assert r.purpose == ""
            assert r.stack == ""
        assert result.test_placement == ""
        assert result.root_files == []

    def test_inventory_with_only_root_level_files_yields_no_roots(self):
        # Every path lives at workspace root → no source root to derive.
        block = """\
```json
{"files": [
    {"path": "index.html"},
    {"path": "style.css"},
    {"path": "package.json"}
]}
```"""
        result = parse_layout(_spec(block))
        assert not result.ok          # falls through to error
        assert not result.has_layout

    def test_layout_block_wins_over_inventory_derivation(self):
        # When both are present, the explicit layout's metadata is used.
        result = parse_layout(_spec(_INVENTORY_BLOCK, _LAYOUT_BLOCK))
        assert result.derived_from_inventory is False
        assert result.roots[0].purpose == "React frontend"


class TestParseLayoutBareFence:
    """Some LLMs forget the ```json tag — the bare-fence fallback should
    still pick up a valid block."""

    def test_bare_fence_fallback(self):
        bare = """\
```
{"workspace_layout": {"roots": [{"path": "app"}]}}
```"""
        result = parse_layout(_spec(bare))
        assert result.has_layout
        assert [r.path for r in result.roots] == ["app"]


class TestLayoutRoot:
    """The dataclass's from_dict normalisation is on the critical path
    so it gets its own coverage."""

    def test_strips_trailing_slash(self):
        assert LayoutRoot.from_dict({"path": "client/"}).path == "client"

    def test_strips_leading_dot_slash(self):
        assert LayoutRoot.from_dict({"path": "./client"}).path == "client"

    def test_rejects_empty_path(self):
        assert LayoutRoot.from_dict({"path": ""}) is None
        assert LayoutRoot.from_dict({"path": "  "}) is None

    def test_rejects_non_dict(self):
        assert LayoutRoot.from_dict("client") is None
        assert LayoutRoot.from_dict(None) is None

    def test_rejects_hidden_dir(self):
        assert LayoutRoot.from_dict({"path": ".git"}) is None

    def test_optional_fields_default_to_empty_string(self):
        r = LayoutRoot.from_dict({"path": "src"})
        assert r is not None
        assert r.purpose == ""
        assert r.stack == ""
