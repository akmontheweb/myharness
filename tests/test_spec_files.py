"""Tests for harness.spec_files — the shared product-spec /
change-request file helpers (extension whitelist + text extraction).
"""
from __future__ import annotations

import pytest

from harness.spec_files import (
    SPEC_FILE_EXTS,
    is_spec_file,
    list_spec_files,
    read_spec_file,
)
from tests.test_product_spec_and_new_build import _make_minimal_pdf_with_text


class TestIsSpecFile:

    @pytest.mark.parametrize("name", [
        "spec.txt", "spec.md", "spec.pdf",
        "spec.TXT", "spec.MD", "spec.PDF",   # case-insensitive
        "with.dots.txt",
    ])
    def test_accepted(self, name):
        assert is_spec_file(name)

    @pytest.mark.parametrize("name", [
        "spec.json", "spec.yaml", "spec", "spec.rst", "README",
    ])
    def test_rejected(self, name):
        assert not is_spec_file(name)

    def test_spec_file_exts_constant_is_stable(self):
        # The constant is referenced by cli.py + graph.py + dashboard.py
        # via mirrored regexes/whitelists. Changing it requires updating
        # every mirror — this test pins the canonical set so the
        # mismatch is caught loudly.
        assert SPEC_FILE_EXTS == (".txt", ".md", ".pdf")


class TestListSpecFiles:

    def test_missing_directory_returns_empty(self, tmp_path):
        assert list_spec_files(str(tmp_path / "absent")) == []

    def test_returns_only_supported_extensions_sorted(self, tmp_path):
        (tmp_path / "zeta.md").write_text("x")
        (tmp_path / "alpha.txt").write_text("x")
        (tmp_path / "beta.pdf").write_bytes(b"%PDF-1.4\n")
        (tmp_path / "ignored.json").write_text("x")
        (tmp_path / "no-ext").write_text("x")
        (tmp_path / "applied").mkdir()        # exclude target
        (tmp_path / "applied" / "old.txt").write_text("x")
        assert list_spec_files(
            str(tmp_path), exclude=frozenset({"applied"}),
        ) == ["alpha.txt", "beta.pdf", "zeta.md"]

    def test_skips_subdirectories_that_happen_to_have_spec_extension(self, tmp_path):
        # `looks-like.md/` is a directory, not a file, so it must not
        # appear in the listing — protects against operator typos that
        # would otherwise crash the consolidator trying to open it.
        (tmp_path / "looks-like.md").mkdir()
        (tmp_path / "real.txt").write_text("x")
        assert list_spec_files(str(tmp_path)) == ["real.txt"]


class TestReadSpecFile:

    def test_reads_txt_as_utf8(self, tmp_path):
        p = tmp_path / "spec.txt"
        p.write_text("plain UTF-8 body")
        assert read_spec_file(str(p)) == "plain UTF-8 body"

    def test_reads_md_as_utf8(self, tmp_path):
        p = tmp_path / "spec.md"
        p.write_text("# heading\n\nbody\n")
        assert read_spec_file(str(p)) == "# heading\n\nbody\n"

    def test_replaces_invalid_utf8_instead_of_raising(self, tmp_path):
        # `errors='replace'` keeps consolidation moving when a stray byte
        # shows up. The replacement char varies by Python version, but
        # the surrounding valid text must survive.
        p = tmp_path / "spec.txt"
        p.write_bytes(b"head\xffbody")
        out = read_spec_file(str(p))
        assert "head" in out and "body" in out

    def test_extracts_text_from_pdf(self, tmp_path):
        p = tmp_path / "design.pdf"
        p.write_bytes(_make_minimal_pdf_with_text("Hello from PDF."))
        out = read_spec_file(str(p))
        assert "Hello from PDF." in out

    def test_pdf_parse_failure_raises_value_error(self, tmp_path):
        # Garbage bytes with a .pdf extension should surface a clear
        # ValueError to the caller instead of bubbling up pypdf's
        # exception class — keeps callers' except clauses tight.
        p = tmp_path / "broken.pdf"
        p.write_bytes(b"not a pdf at all")
        with pytest.raises(ValueError, match="could not extract text"):
            read_spec_file(str(p))
