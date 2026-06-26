"""Shared helpers for product-spec and change-request file ingestion.

One source of truth for which file extensions count as a "spec file"
(product spec or change request) and how to extract their text content.

cli.py and graph.py both scan operator-supplied folders for spec files
and consolidate the contents before handing them to the LLM. Without a
shared helper, each call site re-implemented the ``.txt``-only filter
and re-opened the file as UTF-8, which made adding ``.md``/``.pdf``
support a multi-site change and risked drift between them.
"""
from __future__ import annotations

import os

SPEC_FILE_EXTS: tuple[str, ...] = (".txt", ".md", ".pdf")


def is_spec_file(filename: str) -> bool:
    """True when ``filename`` has one of the allowed spec extensions
    (case-insensitive)."""
    return filename.lower().endswith(SPEC_FILE_EXTS)


def list_spec_files(directory: str, *, exclude: frozenset[str] = frozenset()) -> list[str]:
    """Return the sorted basenames of spec files at the top of ``directory``.

    Skips entries in ``exclude`` (the change_requests archive subdir is the
    only real-world caller) and anything that is not a regular file with
    a supported extension. Returns ``[]`` when the directory is missing
    or unreadable so callers can treat "no spec files" uniformly.
    """
    if not os.path.isdir(directory):
        return []
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    out: list[str] = []
    for name in sorted(entries):
        if name in exclude:
            continue
        if not is_spec_file(name):
            continue
        if not os.path.isfile(os.path.join(directory, name)):
            continue
        out.append(name)
    return out


def read_spec_file(path: str) -> str:
    """Extract text from a product-spec / change-request file.

    ``.txt`` and ``.md`` are read as UTF-8 with ``errors='replace'`` so
    stray bytes do not abort consolidation. ``.pdf`` is parsed via
    :mod:`pypdf` and the text of every page is joined by blank lines.

    Raises :class:`OSError` on read failure and :class:`ValueError` when
    PDF parsing fails or ``pypdf`` is missing (which should not happen
    under a normal install — it ships as a required dependency).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError(
                f"PDF spec file {path!r} requires the `pypdf` package; "
                "reinstall teane to pick up the bundled dependency."
            ) from exc
        try:
            reader = PdfReader(path)
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:
            raise ValueError(f"could not extract text from {path!r}: {exc}") from exc
        return "\n\n".join(p.rstrip() for p in pages).strip() + "\n"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()
