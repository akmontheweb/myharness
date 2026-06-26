"""Tests for harness/impact.py — impact analysis basics."""

import tempfile


from harness.impact import (
    ImpactResult,
    DependencyGraph,
    ImpactAnalyzer,
)


class TestImpactResult:
    """Test ImpactResult dataclass."""

    def test_construct_minimal(self):
        """Construct ImpactResult with required field."""
        result = ImpactResult(modified_files=["a.py"])
        assert result.modified_files == ["a.py"]
        assert result.impacted_files == []
        assert result.total_impacted == 0
        assert result.graph_incomplete is False
        assert result.files_scanned == 0

    def test_construct_with_impacted_files(self):
        """Construct with impacted files list."""
        result = ImpactResult(
            modified_files=["a.py"],
            impacted_files=["b.py", "c.py"],
            total_impacted=2,
            files_scanned=10,
        )
        assert result.modified_files == ["a.py"]
        assert result.impacted_files == ["b.py", "c.py"]
        assert result.total_impacted == 2

    def test_has_impact_with_impacted_files(self):
        """has_impact should return True when impacted files exist."""
        result = ImpactResult(
            modified_files=["a.py"],
            impacted_files=["b.py"],
            total_impacted=1,
        )
        assert result.has_impact() is True

    def test_has_impact_no_impacted_files(self):
        """has_impact should return False when no impacted files."""
        result = ImpactResult(
            modified_files=["a.py"],
            impacted_files=[],
            total_impacted=0,
        )
        assert result.has_impact() is False

    def test_incomplete_flag(self):
        """graph_incomplete flag should be set correctly."""
        result = ImpactResult(
            modified_files=["a.py"],
            impacted_files=[],
            graph_incomplete=True,
            files_scanned=500,
        )
        assert result.graph_incomplete is True
        assert result.files_scanned == 500

    def test_symbol_impact_mapping(self):
        """symbol_impact should map symbols to affected files."""
        result = ImpactResult(
            modified_files=["a.py"],
            impacted_files=["b.py"],
            symbol_impact={
                "MyClass.method": ["b.py", "c.py"],
                "helper_func": ["d.py"],
            },
        )
        assert "MyClass.method" in result.symbol_impact
        assert result.symbol_impact["MyClass.method"] == ["b.py", "c.py"]

    def test_warning_message(self):
        """warning field should store warning text."""
        warning_text = "Analysis incomplete: scanned 100 of ~1000 files"
        result = ImpactResult(
            modified_files=["a.py"],
            warning=warning_text,
        )
        assert result.warning == warning_text


class TestDependencyGraphBasics:
    """Test DependencyGraph initialization."""

    def test_graph_init_with_workspace(self):
        """Graph should initialize with workspace path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            graph = DependencyGraph(tmpdir)
            assert graph is not None

    def test_graph_init_with_max_scan_files(self):
        """Graph should accept max_scan_files parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            graph = DependencyGraph(tmpdir, max_scan_files=1000)
            assert graph is not None

    def test_graph_init_with_ignore_patterns(self):
        """Graph should accept ignore_patterns parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            graph = DependencyGraph(
                tmpdir,
                ignore_patterns=["*.test.py", "__pycache__"],
            )
            assert graph is not None


class TestImpactAnalyzerBasics:
    """Test ImpactAnalyzer initialization."""

    def test_analyzer_init_with_workspace(self):
        """Analyzer should initialize with workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = ImpactAnalyzer(tmpdir)
            assert analyzer is not None

    def test_analyzer_init_with_max_scan_files(self):
        """Analyzer should accept max_scan_files parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = ImpactAnalyzer(tmpdir, max_scan_files=100)
            assert analyzer is not None

    def test_analyzer_analyze_returns_impact_result(self):
        """analyze() should return an ImpactResult."""
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = ImpactAnalyzer(tmpdir)
            result = analyzer.analyze(modified_files=[])
            assert isinstance(result, ImpactResult)

    def test_analyzer_analyze_empty_list(self):
        """analyze() with empty modified_files should work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = ImpactAnalyzer(tmpdir)
            result = analyzer.analyze(modified_files=[])
            assert result.files_scanned >= 0

    def test_analyzer_analyze_with_modified_files(self):
        """analyze() with modified_files should analyze."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file
            test_py = f"{tmpdir}/test.py"
            with open(test_py, "w") as f:
                f.write("x = 1\n")

            analyzer = ImpactAnalyzer(tmpdir)
            result = analyzer.analyze(modified_files=["test.py"])
            assert isinstance(result, ImpactResult)


# ---------------------------------------------------------------------------
# Tree-sitter grammar dispatch — one test per language in the stack.
# Locks in that the language pack resolves the right grammar and that
# _extract_symbols_from_ast recognizes the canonical declaration shapes
# for each language.
# ---------------------------------------------------------------------------

class TestTreeSitterDispatch:
    """Verify Bug 4 is properly fixed for every language in the stack."""

    def _extract(self, lang, source):
        graph = DependencyGraph(workspace_path="/tmp")
        symbols: set[str] = set()
        ok = graph._try_tree_sitter_extract(f"sample.{lang}", source, lang, symbols)
        return ok, symbols

    def test_python_grammar_extracts_function(self):
        ok, symbols = self._extract("python", "def my_fn():\n    pass\n\nclass MyCls:\n    pass\n")
        assert ok is True
        assert "my_fn" in symbols
        assert "MyCls" in symbols

    def test_javascript_grammar_extracts_function_and_class(self):
        ok, symbols = self._extract(
            "javascript",
            "function foo() { return 1; }\nclass Bar { baz() {} }\n",
        )
        assert ok is True
        assert "foo" in symbols
        assert "Bar" in symbols

    def test_typescript_grammar_extracts_typed_declarations(self):
        ok, symbols = self._extract(
            "typescript",
            "function add(a: number, b: number): number { return a + b; }\n"
            "class Repo<T> { items: T[] = []; }\n",
        )
        assert ok is True
        assert "add" in symbols
        assert "Repo" in symbols

    def test_java_grammar_extracts_class_and_method(self):
        ok, symbols = self._extract(
            "java",
            "public class UserService {\n  public String greet(String name) { return name; }\n}\n",
        )
        assert ok is True
        assert "UserService" in symbols
        assert "greet" in symbols

    def test_go_grammar_extracts_function_and_struct(self):
        ok, symbols = self._extract(
            "go",
            "package main\n\nfunc HelloWorld() {}\n\ntype User struct {\n  Name string\n}\n",
        )
        assert ok is True
        assert "HelloWorld" in symbols
        assert "User" in symbols

    def test_rust_grammar_extracts_function_and_struct(self):
        ok, symbols = self._extract(
            "rust",
            "pub fn handler() -> u32 { 0 }\n\npub struct Config { pub port: u16 }\n",
        )
        assert ok is True
        assert "handler" in symbols
        assert "Config" in symbols

    def test_dart_grammar_extracts_class(self):
        ok, symbols = self._extract(
            "dart",
            "class CounterWidget extends StatelessWidget {\n"
            "  Widget build(BuildContext context) => Text('x');\n"
            "}\n",
        )
        assert ok is True
        assert "CounterWidget" in symbols

    def test_unknown_language_returns_false(self):
        # Languages not in _GRAMMAR_NAMES (e.g. C/C++ that we haven't wired
        # AST extraction for yet) return False so the caller uses regex.
        graph = DependencyGraph(workspace_path="/tmp")
        symbols: set[str] = set()
        ok = graph._try_tree_sitter_extract("sample.c", "int main() {}", "c", symbols)
        assert ok is False


class TestWorkspaceStackDetector:
    """_detect_workspace_stack drives language-aware skill filtering."""

    def test_empty_workspace_yields_no_tags(self):
        from harness.impact import _detect_workspace_stack
        with tempfile.TemporaryDirectory() as tmp:
            assert _detect_workspace_stack(tmp) == set()

    def test_nonexistent_path_yields_no_tags(self):
        from harness.impact import _detect_workspace_stack
        assert _detect_workspace_stack("/nonexistent_xyz_path") == set()

    def test_fastapi_workspace_detected(self):
        import os
        from harness.impact import _detect_workspace_stack
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "pyproject.toml"), "w") as f:
                f.write('dependencies = ["fastapi>=0.115", "psycopg[binary]>=3.2"]')
            tags = _detect_workspace_stack(tmp)
            assert "python" in tags
            assert "fastapi" in tags
            assert "postgres" in tags
            # Should NOT pick up django for a fastapi project
            assert "django" not in tags

    def test_django_workspace_detected_by_manage_py(self):
        import os
        from harness.impact import _detect_workspace_stack
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "manage.py"), "w") as f:
                f.write("#!/usr/bin/env python\n")
            tags = _detect_workspace_stack(tmp)
            assert "python" in tags
            assert "django" in tags

    def test_spring_boot_workspace_detected(self):
        import os
        from harness.impact import _detect_workspace_stack
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "pom.xml"), "w") as f:
                f.write(
                    '<project><dependencies>'
                    '<dependency><artifactId>spring-boot-starter-web</artifactId></dependency>'
                    '</dependencies></project>'
                )
            tags = _detect_workspace_stack(tmp)
            assert "java" in tags
            assert "spring" in tags
            assert "maven" in tags

    def test_react_node_workspace_detected(self):
        import json
        import os
        from harness.impact import _detect_workspace_stack
        with tempfile.TemporaryDirectory() as tmp:
            pkg = {
                "name": "my-app",
                "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"},
                "devDependencies": {"vite": "^5.0.0"},
            }
            with open(os.path.join(tmp, "package.json"), "w") as f:
                json.dump(pkg, f)
            tags = _detect_workspace_stack(tmp)
            assert "node" in tags
            assert "react" in tags
            assert "vue" not in tags
            assert "angular" not in tags

    def test_docker_compose_postgres_detected(self):
        import os
        from harness.impact import _detect_workspace_stack
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "docker-compose.yml"), "w") as f:
                f.write("services:\n  db:\n    image: postgres:16\n")
            tags = _detect_workspace_stack(tmp)
            assert "postgres" in tags

    def test_redis_word_boundary_not_fooled_by_redirect(self):
        # Make sure the word-boundary regex doesn't mark a workspace as
        # using Redis just because it has "redirect" in its deps.
        import os
        from harness.impact import _detect_workspace_stack
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "requirements.txt"), "w") as f:
                f.write("django-redirect-urls==1.0.0\n")
            tags = _detect_workspace_stack(tmp)
            assert "redis" not in tags
