"""Tests for the line-coordinate patch primitives.

INSERT_AT_LINE and REPLACE_LINE_RANGE are the Layer-1 enablers for
scanner-driven autofix: semgrep / ESLint / modern Trivy ship fix
metadata with start.line / end.line coordinates that the LLM repair
loop has no clean way to consume via anchor-based ops. These primitives
let the autofix dispatcher hand the patcher the exact range to
overwrite, plus a sha256 hash of the file at the moment the coordinates
were chosen so a sibling patch can't cause a silent wrong-line edit.
"""
from __future__ import annotations

import asyncio
import os

from harness.patcher import (
    OperationType,
    TextPatcher,
    parse_patch_blocks,
    sha256_file_bytes,
)


def _run(coro):
    return asyncio.run(coro)


def _seed(tmp_path, rel: str, body: str) -> str:
    abs_path = os.path.join(str(tmp_path), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(body)
    return abs_path


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# INSERT_AT_LINE — happy paths
# ---------------------------------------------------------------------------

def test_insert_at_line_middle(tmp_path):
    body = "line 1\nline 2\nline 3\n"
    abs_path = _seed(tmp_path, "Dockerfile", body)
    patcher = TextPatcher(str(tmp_path))
    result = _run(patcher.insert_at_line(
        "Dockerfile", line=3, content="USER 1000:1000",
    ))
    assert result.success
    assert result.lines_changed == 1
    assert _read(abs_path) == "line 1\nline 2\nUSER 1000:1000\nline 3\n"


def test_insert_at_line_prepend(tmp_path):
    body = "first\nsecond\n"
    abs_path = _seed(tmp_path, "x.py", body)
    patcher = TextPatcher(str(tmp_path))
    result = _run(patcher.insert_at_line("x.py", line=1, content="# header"))
    assert result.success
    assert _read(abs_path) == "# header\nfirst\nsecond\n"


def test_insert_at_line_append(tmp_path):
    body = "first\nsecond\n"
    abs_path = _seed(tmp_path, "x.py", body)
    patcher = TextPatcher(str(tmp_path))
    # line == len(file_lines) + 1 means "append".
    result = _run(patcher.insert_at_line("x.py", line=3, content="third"))
    assert result.success
    assert _read(abs_path) == "first\nsecond\nthird\n"


def test_insert_at_line_multi_line_content(tmp_path):
    body = "a\nb\nc\n"
    abs_path = _seed(tmp_path, "x.txt", body)
    patcher = TextPatcher(str(tmp_path))
    result = _run(patcher.insert_at_line(
        "x.txt", line=2, content="X1\nX2\nX3",
    ))
    assert result.success
    assert result.lines_changed == 3
    assert _read(abs_path) == "a\nX1\nX2\nX3\nb\nc\n"


# ---------------------------------------------------------------------------
# INSERT_AT_LINE — rejection paths
# ---------------------------------------------------------------------------

def test_insert_at_line_zero_line_rejected(tmp_path):
    _seed(tmp_path, "x.py", "a\n")
    patcher = TextPatcher(str(tmp_path))
    result = _run(patcher.insert_at_line("x.py", line=0, content="z"))
    assert not result.success
    assert "line must be >= 1" in (result.error or "")


def test_insert_at_line_past_end_rejected(tmp_path):
    _seed(tmp_path, "x.py", "a\nb\n")  # 2 lines
    patcher = TextPatcher(str(tmp_path))
    # line 4 is past end+1 (which is 3).
    result = _run(patcher.insert_at_line("x.py", line=4, content="z"))
    assert not result.success
    assert "past end of file" in (result.error or "")


def test_insert_at_line_missing_file(tmp_path):
    patcher = TextPatcher(str(tmp_path))
    result = _run(patcher.insert_at_line("ghost.py", line=1, content="z"))
    assert not result.success
    assert "File not found" in (result.error or "")


def test_insert_at_line_idempotent(tmp_path):
    body = "a\nb\nc\n"
    abs_path = _seed(tmp_path, "x.py", body)
    patcher = TextPatcher(str(tmp_path))
    # First run: real insert.
    r1 = _run(patcher.insert_at_line("x.py", line=2, content="MID"))
    assert r1.success and not r1.no_op
    # Second run with the same content at the same line: file already
    # contains "MID" at line 2 — no-op.
    r2 = _run(patcher.insert_at_line("x.py", line=2, content="MID"))
    assert r2.success
    assert r2.no_op
    assert _read(abs_path) == "a\nMID\nb\nc\n"


def test_insert_at_line_hash_mismatch_rejected(tmp_path):
    abs_path = _seed(tmp_path, "x.py", "a\nb\nc\n")
    patcher = TextPatcher(str(tmp_path))
    # Mutate the file out from under the expected hash.
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write("DIFFERENT CONTENT\n")
    result = _run(patcher.insert_at_line(
        "x.py", line=2, content="MID",
        expected_file_hash="0" * 64,  # certainly wrong
    ))
    assert not result.success
    assert "hash drift" in (result.error or "").lower()


def test_insert_at_line_hash_matches_allows_patch(tmp_path):
    abs_path = _seed(tmp_path, "x.py", "a\nb\nc\n")
    patcher = TextPatcher(str(tmp_path))
    digest = sha256_file_bytes(abs_path)
    assert digest
    result = _run(patcher.insert_at_line(
        "x.py", line=2, content="MID",
        expected_file_hash=digest,
    ))
    assert result.success


# ---------------------------------------------------------------------------
# REPLACE_LINE_RANGE
# ---------------------------------------------------------------------------

def test_replace_line_range_single_line(tmp_path):
    body = "first\nbad\nlast\n"
    abs_path = _seed(tmp_path, "x.py", body)
    patcher = TextPatcher(str(tmp_path))
    result = _run(patcher.replace_line_range(
        "x.py", start_line=2, end_line=2, content="good",
    ))
    assert result.success
    assert _read(abs_path) == "first\ngood\nlast\n"


def test_replace_line_range_multi_line(tmp_path):
    body = "a\nb\nc\nd\n"
    abs_path = _seed(tmp_path, "x.txt", body)
    patcher = TextPatcher(str(tmp_path))
    # Replace lines 2-3 ("b\nc\n") with "X\nY\nZ".
    result = _run(patcher.replace_line_range(
        "x.txt", start_line=2, end_line=3, content="X\nY\nZ",
    ))
    assert result.success
    assert _read(abs_path) == "a\nX\nY\nZ\nd\n"


def test_replace_line_range_invalid_range(tmp_path):
    _seed(tmp_path, "x.txt", "a\nb\n")
    patcher = TextPatcher(str(tmp_path))
    result = _run(patcher.replace_line_range(
        "x.txt", start_line=3, end_line=2, content="z",
    ))
    assert not result.success
    assert "invalid range" in (result.error or "").lower()


def test_replace_line_range_past_end_rejected(tmp_path):
    _seed(tmp_path, "x.txt", "a\nb\n")
    patcher = TextPatcher(str(tmp_path))
    result = _run(patcher.replace_line_range(
        "x.txt", start_line=1, end_line=5, content="z",
    ))
    assert not result.success
    assert "past end" in (result.error or "").lower()


def test_replace_line_range_idempotent(tmp_path):
    body = "a\nb\nc\n"
    abs_path = _seed(tmp_path, "x.txt", body)
    patcher = TextPatcher(str(tmp_path))
    r1 = _run(patcher.replace_line_range(
        "x.txt", start_line=2, end_line=2, content="MID",
    ))
    assert r1.success and not r1.no_op
    # Second run: file already at target state.
    r2 = _run(patcher.replace_line_range(
        "x.txt", start_line=2, end_line=2, content="MID",
    ))
    assert r2.success
    assert r2.no_op
    assert _read(abs_path) == "a\nMID\nc\n"


def test_replace_line_range_hash_drift(tmp_path):
    abs_path = _seed(tmp_path, "x.txt", "a\nb\nc\n")
    patcher = TextPatcher(str(tmp_path))
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write("X\nY\nZ\n")
    result = _run(patcher.replace_line_range(
        "x.txt", start_line=1, end_line=1, content="OK",
        expected_file_hash="0" * 64,
    ))
    assert not result.success
    assert "hash drift" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Parser — LLM-emitted block syntax
# ---------------------------------------------------------------------------

def test_parser_insert_at_line_basic():
    raw = (
        "<<<INSERT_AT_LINE>>>\n"
        "file: Dockerfile\n"
        "line: 19\n"
        "content:\n"
        "USER 1000:1000\n"
        "<<<END_INSERT_AT_LINE>>>\n"
    )
    blocks = parse_patch_blocks(raw)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.operation == OperationType.INSERT_AT_LINE
    assert b.file == "Dockerfile"
    assert b.line == 19
    assert b.content == "USER 1000:1000"
    assert b.expected_file_hash == ""


def test_parser_insert_at_line_with_hash():
    raw = (
        "<<<INSERT_AT_LINE>>>\n"
        "file: client/package.json\n"
        "line: 5\n"
        "hash: deadbeefcafe1234567890\n"
        "content:\n"
        '  "axios": "*",\n'
        "<<<END_INSERT_AT_LINE>>>\n"
    )
    blocks = parse_patch_blocks(raw)
    assert len(blocks) == 1
    assert blocks[0].expected_file_hash == "deadbeefcafe1234567890"
    assert blocks[0].line == 5


def test_parser_replace_line_range_basic():
    raw = (
        "<<<REPLACE_LINE_RANGE>>>\n"
        "file: src/auth.py\n"
        "start_line: 10\n"
        "end_line: 12\n"
        "content:\n"
        "def authenticate(user, pw):\n"
        "    return verify(user, pw)\n"
        "<<<END_REPLACE_LINE_RANGE>>>\n"
    )
    blocks = parse_patch_blocks(raw)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.operation == OperationType.REPLACE_LINE_RANGE
    assert b.line == 10
    assert b.end_line == 12
    assert "def authenticate" in b.content
