"""Tests for the pre-build relative-import resolver."""
from __future__ import annotations

import os

from harness.link_check import (
    BrokenLink,
    broken_links_to_diagnostics,
    scan_workspace_for_broken_imports,
)


def _w(tmp_path, rel: str, body: str) -> None:
    """Write ``body`` to ``tmp_path/rel`` creating parent dirs."""
    abs_path = os.path.join(str(tmp_path), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(body)


# ---------------------------------------------------------------------------
# JS / TS resolution
# ---------------------------------------------------------------------------

def test_js_resolves_static_import_with_extension(tmp_path):
    _w(tmp_path, "src/App.jsx",
       "import LoginForm from './components/LoginForm';\n")
    _w(tmp_path, "src/components/LoginForm.jsx", "export default 1;\n")
    broken = scan_workspace_for_broken_imports(str(tmp_path))
    assert broken == []


def test_js_flags_missing_relative_import(tmp_path):
    # Mirrors the CIOD failure mode: imports './components/Dashboard'
    # but only ./components/DashboardPage exists.
    _w(tmp_path, "src/App.jsx",
       "import Dashboard from './components/Dashboard';\n"
       "import ProductDashboard from './components/DashboardPage';\n")
    _w(tmp_path, "src/components/DashboardPage.jsx", "export default 1;\n")
    broken = scan_workspace_for_broken_imports(str(tmp_path))
    assert len(broken) == 1
    assert broken[0].import_spec == "./components/Dashboard"
    assert broken[0].source_file.endswith("App.jsx")
    assert broken[0].language == "js"


def test_js_resolves_directory_index(tmp_path):
    # `import X from './components'` resolves to `./components/index.{js,jsx,...}`
    _w(tmp_path, "src/App.jsx",
       "import Components from './components';\n")
    _w(tmp_path, "src/components/index.jsx", "export default 1;\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


def test_js_handles_side_effect_import(tmp_path):
    _w(tmp_path, "src/main.js", "import './polyfills';\n")
    # No polyfills file → flagged
    broken = scan_workspace_for_broken_imports(str(tmp_path))
    assert len(broken) == 1
    assert broken[0].import_spec == "./polyfills"


def test_js_handles_commonjs_require(tmp_path):
    _w(tmp_path, "lib/index.js",
       "const utils = require('./utils');\n")
    _w(tmp_path, "lib/utils.js", "module.exports = {};\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


def test_js_ignores_bare_node_module_imports(tmp_path):
    # `import React from 'react'` resolves via node_modules — out of
    # scope for link_check (missing-dep autofix handles it).
    _w(tmp_path, "src/App.jsx",
       "import React from 'react';\n"
       "import { toast } from 'react-hot-toast';\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


def test_js_resolves_typescript_path(tmp_path):
    _w(tmp_path, "src/index.ts",
       "import { Foo } from './foo';\n")
    _w(tmp_path, "src/foo.ts", "export const Foo = 1;\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


def test_js_resolves_dynamic_import(tmp_path):
    _w(tmp_path, "src/lazy.js",
       "const Mod = await import('./big-module');\n")
    _w(tmp_path, "src/big-module.js", "export default 1;\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


def test_js_resolves_explicit_extension(tmp_path):
    # When the import writes ".jsx" explicitly, only the literal path is
    # tried — extension fallbacks must still work the same.
    _w(tmp_path, "src/index.js",
       "import './styles/tailwind.css';\n")
    _w(tmp_path, "src/styles/tailwind.css", "/* css */\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


def test_js_handles_parent_directory_traversal(tmp_path):
    _w(tmp_path, "src/components/Button.jsx",
       "import { cn } from '../utils/cn';\n")
    _w(tmp_path, "src/utils/cn.js", "export const cn = (x) => x;\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


def test_js_skips_node_modules(tmp_path):
    # Make sure the walker doesn't descend into node_modules even if
    # there's a broken relative import inside it.
    _w(tmp_path, "node_modules/some-pkg/index.js",
       "import x from './does-not-exist';\n")
    _w(tmp_path, "src/App.jsx", "export default 1;\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# Python resolution
# ---------------------------------------------------------------------------

def test_python_resolves_sibling_relative_import(tmp_path):
    _w(tmp_path, "pkg/__init__.py", "")
    _w(tmp_path, "pkg/main.py", "from .util import helper\n")
    _w(tmp_path, "pkg/util.py", "def helper(): pass\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


def test_python_flags_missing_relative_import(tmp_path):
    _w(tmp_path, "pkg/__init__.py", "")
    _w(tmp_path, "pkg/main.py", "from .missing import helper\n")
    broken = scan_workspace_for_broken_imports(str(tmp_path))
    assert len(broken) == 1
    assert broken[0].language == "python"
    assert ".missing" in broken[0].import_spec


def test_python_resolves_subpackage_via_init(tmp_path):
    _w(tmp_path, "pkg/__init__.py", "")
    _w(tmp_path, "pkg/sub/__init__.py", "")
    _w(tmp_path, "pkg/main.py", "from .sub import x\n")
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# Diagnostic conversion
# ---------------------------------------------------------------------------

def test_broken_links_to_diagnostics_shape():
    broken = [
        BrokenLink(
            source_file="src/App.jsx",
            import_spec="./components/Dashboard",
            searched_paths=("/tmp/Dashboard.jsx", "/tmp/Dashboard/index.jsx"),
            language="js",
        ),
    ]
    diags = broken_links_to_diagnostics(broken)
    assert len(diags) == 1
    d = diags[0]
    assert d["error_code"] == "LINK_BROKEN"
    assert d["file"] == "src/App.jsx"
    assert "./components/Dashboard" in str(d["message"])
    assert d["missing_symbol"] == "./components/Dashboard"
    # Searched paths appear in semantic context for the LLM repair prompt.
    assert "Dashboard.jsx" in str(d["semantic_context"])


def test_audit_empty_workspace_returns_empty():
    # Non-existent workspace path should not crash.
    assert scan_workspace_for_broken_imports("/nonexistent/path/xyz") == []


def test_audit_handles_unreadable_file_gracefully(tmp_path):
    # File with garbage bytes — read with errors='replace' should not raise.
    bad = os.path.join(str(tmp_path), "src", "bad.js")
    os.makedirs(os.path.dirname(bad))
    with open(bad, "wb") as f:
        f.write(b"\x00\xff\xfe binary garbage \x80")
    # Should return cleanly (no broken imports detected, no exception).
    assert scan_workspace_for_broken_imports(str(tmp_path)) == []
