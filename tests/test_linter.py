import textwrap

from healer.tools.linter import (
    LintIssue,
    LintResult,
    detect_lint_command,
    format_issues,
    lint_delta,
    parse_eslint,
    parse_generic,
    parse_mypy,
    parse_ruff,
    run_lint,
)


def test_parse_ruff_full_format():
    output = textwrap.dedent("""
        F401 [*] `os` imported but unused
         --> src/auth.py:1:8
          |
        1 | import os
          |        ^^
        help: Remove unused import: `os`

        Found 1 error.
    """)
    issues = parse_ruff(output)
    assert len(issues) == 1
    assert issues[0].file == "src/auth.py"
    assert issues[0].line == 1
    assert issues[0].code == "F401"


def test_parse_ruff_basic():
    output = textwrap.dedent("""
        src/auth.py:12:1: F401 [*] `os` imported but unused
        src/auth.py:40:9: E501 Line too long (120 > 100)
        Found 2 errors.
    """)
    issues = parse_ruff(output)
    assert len(issues) == 2
    assert issues[0].file == "src/auth.py"
    assert issues[0].line == 12
    assert issues[0].code == "F401"
    assert "imported but unused" in issues[0].message
    assert issues[0].tool == "ruff"


def test_parse_ruff_clean():
    assert parse_ruff("All checks passed!") == []


def test_parse_mypy_with_error_code():
    output = "healer/loop.py:41: error: Missing return statement  [return]\n"
    issues = parse_mypy(output)
    assert len(issues) == 1
    assert issues[0].code == "return"
    assert issues[0].severity == "error"
    assert issues[0].line == 41


def test_parse_mypy_severities():
    output = textwrap.dedent("""
        a.py:1: error: Incompatible types  [assignment]
        a.py:2: warning: Unused ignore
        a.py:3: note: See docs
    """)
    sevs = [i.severity for i in parse_mypy(output)]
    assert sevs == ["error", "warning", "note"]


def test_parse_eslint_stylish():
    output = textwrap.dedent("""
        /repo/src/app.js
          12:3  error  'x' is assigned a value but never used  no-unused-vars
          14:1  warning  Unexpected console statement  no-console

        ✖ 2 problems
    """)
    issues = parse_eslint(output)
    assert len(issues) == 2
    assert issues[0].file == "/repo/src/app.js"
    assert issues[0].code == "no-unused-vars"
    assert issues[1].severity == "warning"


def test_parse_eslint_ignores_issues_without_file_header():
    output = "  12:3  error  orphan issue  no-unused-vars\n"
    assert parse_eslint(output) == []


def test_parse_generic_fallback():
    issues = parse_generic("weirdlint.py:7: something smells\n")
    assert len(issues) == 1
    assert issues[0].code == "generic"
    assert issues[0].line == 7


def test_issue_key_ignores_line_number():
    a = LintIssue(file="a.py", line=10, code="F401", message="unused")
    b = LintIssue(file="a.py", line=99, code="F401", message="unused")
    assert a.key == b.key


def _issue(code="F401", file="a.py", line=1, msg="unused", severity="error"):
    return LintIssue(file=file, line=line, code=code, message=msg, severity=severity)


def _result(issues):
    return LintResult(exit_code=1 if issues else 0, output="", issues=issues, tool="ruff")


def test_lint_delta_detects_introduced_issue():
    before = _result([_issue("F401")])
    after = _result([_issue("F401"), _issue("E501", msg="line too long")])
    delta = lint_delta(before, after)
    assert [i.code for i in delta.introduced] == ["E501"]
    assert delta.resolved == []
    assert delta.is_regression


def test_lint_delta_detects_resolved_issue():
    delta = lint_delta(_result([_issue("F401")]), _result([]))
    assert [i.code for i in delta.resolved] == ["F401"]
    assert not delta.is_regression


def test_lint_delta_ignores_line_shift():
    before = _result([_issue(line=10)])
    after = _result([_issue(line=42)])
    delta = lint_delta(before, after)
    assert delta.introduced == []
    assert delta.resolved == []
    assert not delta.is_regression


def test_lint_delta_warning_only_is_not_regression():
    after = _result([_issue("E501", severity="warning", msg="long")])
    assert not lint_delta(_result([]), after).is_regression


def test_run_lint_reports_issues(tmp_path):
    (tmp_path / "bad.py").write_text("import os\n")
    result = run_lint("ruff check . --select F401 --no-cache", str(tmp_path))
    assert result.tool == "ruff"
    assert result.exit_code != 0
    assert any(i.code == "F401" for i in result.issues)


def test_run_lint_clean_repo(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n")
    result = run_lint("ruff check . --select F401 --no-cache", str(tmp_path))
    assert result.exit_code == 0
    assert result.issues == []
    assert "clean" in result.summary()


def test_run_lint_missing_tool_is_not_fatal(tmp_path):
    result = run_lint("definitely-not-a-real-linter .", str(tmp_path))
    assert result.issues == []
    assert result.exit_code != 0


def test_run_lint_timeout(tmp_path):
    result = run_lint("sleep 5", str(tmp_path), timeout=1)
    assert result.output == "TIMEOUT"
    assert result.issues == []


def test_detect_lint_command_python_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_lint_command(str(tmp_path)) == "ruff check ."


def test_detect_lint_command_unknown_repo(tmp_path):
    assert detect_lint_command(str(tmp_path)) is None


def test_issues_in_filters_by_file():
    result = _result([_issue(file="src/auth.py"), _issue(file="src/db.py", code="E501")])
    assert [i.file for i in result.issues_in(["auth.py"])] == ["src/auth.py"]


def test_format_issues_truncates():
    issues = [_issue(line=n, msg=f"m{n}") for n in range(30)]
    text = format_issues(issues, limit=5)
    assert text.count("\n") == 5
    assert "and 25 more" in text


def test_format_issues_empty():
    assert format_issues([]) == "(none)"
