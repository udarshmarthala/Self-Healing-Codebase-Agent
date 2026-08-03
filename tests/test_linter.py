import textwrap

from healer.tools.linter import (
    LintIssue,
    parse_eslint,
    parse_generic,
    parse_mypy,
    parse_ruff,
)


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
