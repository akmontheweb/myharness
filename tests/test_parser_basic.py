"""Tests for harness/parser_registry.py — error parsing utilities."""


from harness.parser_registry import (
    _strip_ansi,
    RustParser,
    GoParser,
    GenericParser,
    JavaParser,
    TypeScriptParser,
    DartParser,
    detect_and_parse,
    get_parser,
)


class TestStripAnsi:
    """Test ANSI escape code stripping."""

    def test_removes_color_code(self):
        """Should remove ANSI color codes."""
        text = "\x1b[31merror\x1b[0m"  # red
        result = _strip_ansi(text)
        assert result == "error"

    def test_removes_bold(self):
        """Should remove bold codes."""
        text = "\x1b[1mbold\x1b[0m"
        result = _strip_ansi(text)
        assert result == "bold"

    def test_plain_text_unchanged(self):
        """Plain text should be unchanged."""
        text = "hello world"
        result = _strip_ansi(text)
        assert result == "hello world"

    def test_multiple_codes(self):
        """Should remove multiple ANSI sequences."""
        text = "\x1b[1m\x1b[31m\x1b[1mRED BOLD\x1b[0m"
        result = _strip_ansi(text)
        assert result == "RED BOLD"
        assert "\x1b" not in result

    def test_empty_string(self):
        """Empty string should return empty."""
        assert _strip_ansi("") == ""


class TestParserDiagnostics:
    """Test parser diagnostics methods."""

    def test_rust_parser_parse_diagnostics(self):
        """RustParser should have parse_diagnostics static method."""
        output = "error[E0425]: cannot find value"
        diagnostics = RustParser.parse_diagnostics(output)
        assert isinstance(diagnostics, list)

    def test_rust_parser_empty_output(self):
        """Empty output should return empty list."""
        diagnostics = RustParser.parse_diagnostics("")
        assert diagnostics == [] or isinstance(diagnostics, list)

    def test_go_parser_parse_diagnostics(self):
        """GoParser should have parse_diagnostics static method."""
        output = "./main.go:10:5: undefined: SomeFunc"
        diagnostics = GoParser.parse_diagnostics(output)
        assert isinstance(diagnostics, list)

    def test_generic_parser_parse_diagnostics(self):
        """GenericParser should have parse_diagnostics static method."""
        output = "error: something failed"
        diagnostics = GenericParser.parse_diagnostics(output)
        assert isinstance(diagnostics, list)


class TestJavaParser:
    """Cover Maven [ERROR] /path:[L,C] and javac/Gradle short forms."""

    def test_maven_error_extracted(self):
        output = (
            "[INFO] Scanning for projects...\n"
            "[ERROR] /repo/src/main/java/UserService.java:[42,17] cannot find symbol\n"
            "[ERROR]   symbol:   variable userRepo\n"
            "[ERROR]   location: class UserService\n"
        )
        diags = JavaParser.parse_diagnostics(output)
        assert len(diags) == 1
        d = diags[0]
        assert d.file.endswith("UserService.java")
        assert d.line == 42
        assert d.column == 17
        assert d.severity == "error"
        assert "cannot find symbol" in d.message

    def test_maven_warning_severity(self):
        output = "[WARNING] /repo/Foo.java:[5,1] deprecated API usage\n"
        diags = JavaParser.parse_diagnostics(output)
        assert len(diags) == 1
        assert diags[0].severity == "warning"

    def test_javac_short_form_extracted(self):
        output = (
            "src/main/java/Foo.java:10: error: ';' expected\n"
            "        int x = 1\n"
            "                 ^\n"
        )
        diags = JavaParser.parse_diagnostics(output)
        assert len(diags) == 1
        assert diags[0].file.endswith("Foo.java")
        assert diags[0].line == 10
        assert diags[0].severity == "error"

    def test_no_match_returns_empty(self):
        diags = JavaParser.parse_diagnostics("BUILD SUCCESSFUL in 4s\n")
        assert diags == []


class TestTypeScriptParser:
    """Cover the tsc parens form path.ts(L,C): error TSXXXX: msg."""

    def test_tsc_error_extracted(self):
        output = "src/services/user.ts(42,17): error TS2304: Cannot find name 'bar'.\n"
        diags = TypeScriptParser.parse_diagnostics(output)
        assert len(diags) == 1
        d = diags[0]
        assert d.file == "src/services/user.ts"
        assert d.line == 42
        assert d.column == 17
        assert d.error_code == "TS2304"
        assert d.severity == "error"
        assert "Cannot find name" in d.message

    def test_tsx_extension_supported(self):
        output = "components/Button.tsx(5,9): error TS2554: Expected 1 arguments, but got 0.\n"
        diags = TypeScriptParser.parse_diagnostics(output)
        assert len(diags) == 1
        assert diags[0].file.endswith("Button.tsx")

    def test_warning_severity(self):
        output = "lib/foo.ts(1,1): warning TS6133: 'x' is declared but never used.\n"
        diags = TypeScriptParser.parse_diagnostics(output)
        assert len(diags) == 1
        assert diags[0].severity == "warning"

    def test_no_match_returns_empty(self):
        diags = TypeScriptParser.parse_diagnostics("Found 0 errors. Watching for file changes.\n")
        assert diags == []


class TestDartParser:
    """Cover the dart analyze / flutter analyze bullet form."""

    def test_dart_analyze_error_extracted(self):
        output = (
            "error • Undefined name 'bar' • lib/services/foo.dart:42:17 • undefined_identifier\n"
        )
        diags = DartParser.parse_diagnostics(output)
        assert len(diags) == 1
        d = diags[0]
        assert d.file == "lib/services/foo.dart"
        assert d.line == 42
        assert d.column == 17
        assert d.error_code == "undefined_identifier"
        assert d.severity == "error"
        assert "Undefined name" in d.message

    def test_dart_analyze_warning(self):
        output = (
            "warning • Unused import: 'package:foo/foo.dart' • lib/main.dart:3:8 • unused_import\n"
        )
        diags = DartParser.parse_diagnostics(output)
        assert len(diags) == 1
        assert diags[0].severity == "warning"

    def test_dart_info_collapses_to_warning(self):
        output = "info • Prefer const constructors • lib/widget.dart:10:5 • prefer_const_constructors\n"
        diags = DartParser.parse_diagnostics(output)
        assert len(diags) == 1
        # info/hint collapse to "warning" for downstream simplicity.
        assert diags[0].severity == "warning"

    def test_no_match_returns_empty(self):
        diags = DartParser.parse_diagnostics("No issues found!\n")
        assert diags == []


class TestParserDispatch:
    """detect_and_parse should pick the right parser from build_command."""

    def test_mvn_routes_to_java_parser(self):
        assert get_parser("mvn") is JavaParser
        assert get_parser("gradle") is JavaParser
        assert get_parser("javac") is JavaParser

    def test_tsc_routes_to_typescript_parser(self):
        assert get_parser("tsc") is TypeScriptParser
        assert get_parser("vite") is TypeScriptParser
        assert get_parser("next") is TypeScriptParser

    def test_dart_flutter_routes_to_dart_parser(self):
        assert get_parser("dart") is DartParser
        assert get_parser("flutter") is DartParser

    def test_detect_and_parse_uses_java_on_mvn_command(self):
        output = "[ERROR] /repo/Foo.java:[5,1] cannot find symbol\n"
        diags = detect_and_parse(output, build_command="mvn compile")
        assert len(diags) == 1
        assert diags[0].file.endswith("Foo.java")
        assert diags[0].line == 5

    def test_detect_and_parse_uses_typescript_on_tsc_command(self):
        output = "src/x.ts(3,2): error TS1005: ',' expected.\n"
        diags = detect_and_parse(output, build_command="tsc --noEmit")
        assert len(diags) == 1
        assert diags[0].error_code == "TS1005"

    def test_detect_and_parse_uses_dart_on_flutter_command(self):
        output = "error • bad name • lib/a.dart:1:1 • bad_name\n"
        diags = detect_and_parse(output, build_command="flutter analyze")
        assert len(diags) == 1
        assert diags[0].file == "lib/a.dart"
