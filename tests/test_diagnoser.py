import textwrap
import pytest
from healer.agents.diagnoser import diagnose, _classify_error, _extract_affected_files, _pick_strategy


def test_classify_import_error():
    assert _classify_error("ModuleNotFoundError: No module named 'foo'", []) == "import_error"


def test_classify_assertion_error():
    assert _classify_error("AssertionError: expected True", []) == "assertion_error"


def test_classify_type_error():
    assert _classify_error("TypeError: unsupported operand", []) == "type_error"


def test_classify_unknown():
    assert _classify_error("some random output", []) == "unknown_error"


def test_extract_affected_files(tmp_path):
    output = textwrap.dedent(f"""
        FAILED tests/test_foo.py::test_bar
          File "{tmp_path}/src/auth.py", line 12, in login
    """)
    files = _extract_affected_files(output, str(tmp_path))
    assert any("auth.py" in f for f in files)


def test_pick_strategy_avoids_seen():
    strategies = _pick_strategy("import_error", [{"suggested_strategy": "fix_import"}])
    assert strategies != "fix_import"


def test_diagnose_returns_diagnosis(tmp_path):
    (tmp_path / "test_foo.py").write_text("def test_x(): assert False\n")
    output = textwrap.dedent(f"""
        FAILED test_foo.py::test_x - AssertionError
        E   AssertionError: expected True
          File "{tmp_path}/test_foo.py", line 1
    """)
    d = diagnose(output, ["test_foo.py::test_x"], str(tmp_path))
    assert d.error_type == "assertion_error"
    assert d.failing_tests == ["test_foo.py::test_x"]
    assert d.suggested_strategy != ""
