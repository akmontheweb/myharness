"""Phase 2 tests for ``teane test`` defect ingestion + clustering + CR emission.

Goal: feed canned Playwright JSON with 3 failures sharing one root cause +
1 failure from a different cause → assert exactly 2 CR-DEFECT-* directories
appear, each with the expected attachments and cluster_evidence shape.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from harness.test_defects import (
    Cluster,
    FailureRecord,
    cluster_failures,
    emit_defect_cr,
    parse_playwright_json,
)


# ---------------------------------------------------------------------------
# parse_playwright_json — shape tolerance
# ---------------------------------------------------------------------------


def test_parse_skips_passing_results() -> None:
    blob = {
        "suites": [{
            "specs": [{
                "file": "tests/e2e/login.spec.ts",
                "tests": [{
                    "title": "should pass",
                    "results": [{"status": "passed", "duration": 100}],
                }],
            }],
        }],
    }
    assert parse_playwright_json(blob) == []


def test_parse_extracts_failure_with_attachments() -> None:
    blob = {
        "suites": [{
            "specs": [{
                "file": "tests/e2e/login.spec.ts",
                "tests": [{
                    "title": "rejects empty password",
                    "results": [{
                        "status": "failed",
                        "duration": 230,
                        "error": {
                            "message": "Timed out POST https://api/login",
                            "stack": "at Login (/work/tests/e2e/login.spec.ts:42:18)",
                        },
                        "attachments": [
                            {"name": "trace", "path": "/tmp/trace.zip"},
                            {"name": "screenshot", "path": "/tmp/shot.png"},
                            {"name": "dom", "path": "/tmp/dom.html"},
                        ],
                    }],
                }],
            }],
        }],
    }
    failures = parse_playwright_json(blob)
    assert len(failures) == 1
    f = failures[0]
    assert f.title == "rejects empty password"
    assert f.spec_file == "tests/e2e/login.spec.ts"
    assert "POST https://api/login" in f.error_message
    assert f.attachments == {
        "trace": "/tmp/trace.zip",
        "screenshot": "/tmp/shot.png",
        "dom": "/tmp/dom.html",
    }


def test_parse_walks_nested_suites() -> None:
    blob = {
        "suites": [{
            "suites": [{
                "specs": [{
                    "file": "nested.spec.ts",
                    "tests": [{
                        "title": "nested",
                        "results": [{
                            "status": "failed",
                            "error": {"message": "boom", "stack": ""},
                        }],
                    }],
                }],
            }],
        }],
    }
    assert len(parse_playwright_json(blob)) == 1


# ---------------------------------------------------------------------------
# FailureRecord.cluster_key
# ---------------------------------------------------------------------------


def _fr(
    title: str,
    *,
    stack: str = "",
    error: str = "",
    attachments: dict[str, str] | None = None,
) -> FailureRecord:
    return FailureRecord(
        title=title,
        spec_file="tests/e2e/x.spec.ts",
        error_message=error,
        stack=stack,
        attachments=attachments or {},
    )


def test_cluster_key_uses_first_user_frame() -> None:
    stack = (
        "    at PageMethod (/x/node_modules/playwright/lib/page.js:1:1)\n"
        "    at Click (/work/tests/e2e/login.spec.ts:42:18)\n"
        "    at Other (/work/tests/e2e/login.spec.ts:99:1)\n"
    )
    f = _fr("t", stack=stack)
    frame, _ = f.cluster_key()
    assert frame == "login.spec.ts:42"


def test_cluster_key_falls_back_to_message_hash() -> None:
    f = _fr("t", error="assertion failed: visible() = false")
    frame, _ = f.cluster_key()
    assert frame.startswith("msg:")


def test_cluster_key_extracts_network_call() -> None:
    f = _fr("t", error="Request failed: POST https://api.example.com/login returned 500")
    _, netcall = f.cluster_key()
    assert netcall == "POST https://api.example.com/login"


# ---------------------------------------------------------------------------
# cluster_failures — 3+1 → 2 clusters
# ---------------------------------------------------------------------------


def test_cluster_groups_by_shared_root_cause() -> None:
    shared_stack = "    at Setup (/work/tests/e2e/auth.helper.ts:10:5)\n"
    f1 = _fr("scenario A", stack=shared_stack, error="POST /api/login 500")
    f2 = _fr("scenario B", stack=shared_stack, error="POST /api/login 500")
    f3 = _fr("scenario C", stack=shared_stack, error="POST /api/login 500")
    f4 = _fr(
        "unrelated D",
        stack="    at Other (/work/tests/e2e/checkout.spec.ts:7:1)\n",
        error="POST /api/checkout 500",
    )
    clusters = cluster_failures([f1, f2, f3, f4])
    assert len(clusters) == 2
    sizes = sorted(c.size() for c in clusters)
    assert sizes == [1, 3]


def test_cluster_primary_has_most_attachments() -> None:
    stack = "    at S (/work/tests/e2e/a.spec.ts:1:1)\n"
    f_thin = _fr("a", stack=stack)
    f_rich = _fr("b", stack=stack, attachments={
        "trace": "/t/trace.zip", "screenshot": "/t/shot.png",
    })
    [cluster] = cluster_failures([f_thin, f_rich])
    assert cluster.primary.title == "b"
    assert cluster.evidence[0].title == "a"


# ---------------------------------------------------------------------------
# emit_defect_cr — directory layout + content
# ---------------------------------------------------------------------------


def test_emit_writes_expected_files(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Synthetic attachments on disk so the copy step has something to read.
    trace = tmp_path / "trace.zip"
    trace.write_bytes(b"PK\x03\x04zip-bytes")
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n")
    dom = tmp_path / "dom.html"
    dom.write_text("<html></html>")

    stack = "    at Login (/work/tests/e2e/login.spec.ts:42:18)\n"
    primary = FailureRecord(
        title="rejects empty password",
        spec_file="tests/e2e/login.spec.ts",
        error_message="POST /api/login expected 401 got 500",
        stack=stack,
        attachments={"trace": str(trace), "screenshot": str(shot), "dom": str(dom)},
        source_spec_id="STORY-3.AC-2",
    )
    evidence = [FailureRecord(
        title="rejects whitespace password",
        spec_file="tests/e2e/login.spec.ts",
        error_message="POST /api/login expected 401 got 500",
        stack=stack,
    )]
    cluster = Cluster(key=primary.cluster_key(), primary=primary, evidence=evidence)

    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    cr_dir = emit_defect_cr(cluster, str(workspace), now=now)

    assert os.path.basename(cr_dir).startswith("CR-DEFECT-20260630-rejects-empty-password-")
    for fname in ("narrative.txt", "source_spec.md", "cluster_evidence.json",
                  "trace.zip", "screenshot.png", "dom.html"):
        assert os.path.isfile(os.path.join(cr_dir, fname)), f"missing {fname}"

    narrative = (Path(cr_dir) / "narrative.txt").read_text(encoding="utf-8")
    assert "rejects empty password" in narrative
    assert "Cluster size**: 2" in narrative

    source_spec = (Path(cr_dir) / "source_spec.md").read_text(encoding="utf-8")
    assert "STORY-3.AC-2" in source_spec

    evidence_json = json.loads((Path(cr_dir) / "cluster_evidence.json").read_text())
    assert evidence_json["size"] == 2
    assert evidence_json["primary"]["title"] == "rejects empty password"
    assert evidence_json["evidence"][0]["title"] == "rejects whitespace password"
    assert evidence_json["primary"]["source_spec_id"] == "STORY-3.AC-2"


def test_emit_handles_missing_attachments_gracefully(tmp_path: Path) -> None:
    """No trace/screenshot/dom → narrative + evidence still written."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    primary = FailureRecord(
        title="login slow",
        spec_file="tests/e2e/login.spec.ts",
        error_message="assertion: page.locator() not visible",
        stack="",
    )
    cluster = Cluster(key=primary.cluster_key(), primary=primary)
    cr_dir = emit_defect_cr(cluster, str(workspace))
    assert os.path.isfile(os.path.join(cr_dir, "narrative.txt"))
    assert os.path.isfile(os.path.join(cr_dir, "cluster_evidence.json"))
    # No attachments to copy → those files shouldn't exist.
    assert not os.path.exists(os.path.join(cr_dir, "trace.zip"))
    assert not os.path.exists(os.path.join(cr_dir, "screenshot.png"))


# ---------------------------------------------------------------------------
# End-to-end: 3+1 canned Playwright JSON → 2 CRs on disk
# ---------------------------------------------------------------------------


def test_end_to_end_3_plus_1_produces_2_crs(tmp_path: Path) -> None:
    """The canonical Phase 2 success criterion."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    shared_stack = "    at Setup (/work/tests/e2e/auth.helper.ts:10:5)\n"
    other_stack = "    at Cart (/work/tests/e2e/checkout.spec.ts:7:1)\n"

    blob = {
        "suites": [{
            "specs": [
                {
                    "file": "tests/e2e/login.spec.ts",
                    "tests": [
                        _passing("login form renders"),
                        _failing("login A", shared_stack, "POST /api/login 500"),
                        _failing("login B", shared_stack, "POST /api/login 500"),
                        _failing("login C", shared_stack, "POST /api/login 500"),
                    ],
                },
                {
                    "file": "tests/e2e/checkout.spec.ts",
                    "tests": [
                        _failing("checkout submit", other_stack, "POST /api/checkout 500"),
                    ],
                },
            ],
        }],
    }
    failures = parse_playwright_json(blob)
    assert len(failures) == 4

    clusters = cluster_failures(failures)
    assert len(clusters) == 2

    cr_dirs = [emit_defect_cr(c, str(workspace)) for c in clusters]
    assert len(cr_dirs) == 2
    assert len({os.path.basename(d) for d in cr_dirs}) == 2  # no collisions

    # Larger cluster (3 failures) lands first per clusters' sort.
    big = json.loads((Path(cr_dirs[0]) / "cluster_evidence.json").read_text())
    small = json.loads((Path(cr_dirs[1]) / "cluster_evidence.json").read_text())
    assert big["size"] == 3
    assert small["size"] == 1


def _passing(title: str) -> dict:
    return {"title": title, "results": [{"status": "passed", "duration": 50}]}


def _failing(title: str, stack: str, msg: str) -> dict:
    return {
        "title": title,
        "results": [{
            "status": "failed",
            "duration": 200,
            "error": {"message": msg, "stack": stack},
            "attachments": [],
        }],
    }
