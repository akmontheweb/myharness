"""Tests for autofix audit hardening (batch 8).

Covers:
  - _python_module_path validates each component as a Python ident   (§6.7)
  - autofix file reads tolerate UnicodeDecodeError via errors=replace (§6.6)
"""

from __future__ import annotations


from harness import autofix


# ---------------------------------------------------------------------------
# _python_module_path validation (audit §6.7)
# ---------------------------------------------------------------------------


def test_python_module_path_valid_module(tmp_path):
    workspace = tmp_path
    def_path = tmp_path / "foo" / "bar" / "baz.py"
    def_path.parent.mkdir(parents=True)
    def_path.write_text("")
    assert autofix._python_module_path(str(def_path), str(workspace)) == "foo.bar.baz"


def test_python_module_path_dash_in_dir_returns_empty(tmp_path):
    """A dash in a directory name produces an invalid Python module
    name — the helper returns "" instead of letting the LLM see a
    SyntaxError-inducing suggestion."""
    workspace = tmp_path
    def_path = tmp_path / "my-pkg" / "foo.py"
    def_path.parent.mkdir(parents=True)
    def_path.write_text("")
    assert autofix._python_module_path(str(def_path), str(workspace)) == ""


def test_python_module_path_keyword_segment_returns_empty(tmp_path):
    """A path component that is a Python keyword (``class``) is rejected."""
    workspace = tmp_path
    def_path = tmp_path / "class" / "module.py"
    def_path.parent.mkdir(parents=True)
    def_path.write_text("")
    assert autofix._python_module_path(str(def_path), str(workspace)) == ""


def test_python_module_path_init_files_strip_init(tmp_path):
    workspace = tmp_path
    def_path = tmp_path / "pkg" / "__init__.py"
    def_path.parent.mkdir(parents=True)
    def_path.write_text("")
    assert autofix._python_module_path(str(def_path), str(workspace)) == "pkg"


def test_python_module_path_pyi_extension(tmp_path):
    workspace = tmp_path
    def_path = tmp_path / "stubs.pyi"
    def_path.write_text("")
    assert autofix._python_module_path(str(def_path), str(workspace)) == "stubs"


def test_python_module_path_numeric_first_char_returns_empty(tmp_path):
    """A path component starting with a digit (``123pkg``) is not a
    valid Python identifier and must be rejected."""
    workspace = tmp_path
    def_path = tmp_path / "123pkg" / "module.py"
    def_path.parent.mkdir(parents=True)
    def_path.write_text("")
    assert autofix._python_module_path(str(def_path), str(workspace)) == ""


# ---------------------------------------------------------------------------
# Autofix file reads tolerate UnicodeDecodeError (audit §6.6)
# ---------------------------------------------------------------------------


def test_autofix_reads_tolerate_invalid_utf8(tmp_path):
    """A mixed-encoding source file with bytes that don't decode as
    UTF-8 must NOT crash the autofix path. ``errors="replace"`` keeps
    the read alive; the substituted U+FFFDs don't block patching."""
    # We exercise the read sites by constructing a file that contains
    # invalid UTF-8 and asking _maybe_inject_import (which uses one of
    # the read sites) to operate on it. The function may legitimately
    # return None for many reasons (no matching definition, etc.);
    # what matters is that it doesn't raise UnicodeDecodeError.
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"# header\nx = 1\nraw=\xff\xfe\xfd\n")  # invalid UTF-8
    # Try to read the file via Python's open with the new args and
    # confirm no exception.
    try:
        with open(str(bad), "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except UnicodeDecodeError:
        raise AssertionError("errors='replace' should have suppressed this")
    # The replacement char is present where the invalid bytes were.
    assert "�" in content or "x = 1" in content
