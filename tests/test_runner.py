import textwrap
from unittest.mock import MagicMock, patch

import pytest

from healer.tools.runner import RunResult, _parse_failures, _parse_pytest, run_tests


def test_parse_pytest_failures():
    output = textwrap.dedent("""
        FAILED tests/test_auth.py::test_login - AssertionError: expected True
        FAILED tests/test_auth.py::test_logout
        ERROR tests/test_db.py::test_connection - RuntimeError
    """)
    failures = _parse_pytest(output)
    assert "tests/test_auth.py::test_login" in failures
    assert "tests/test_auth.py::test_logout" in failures
    assert "tests/test_db.py::test_connection" in failures


def test_parse_pytest_no_failures():
    output = "5 passed in 0.12s"
    failures = _parse_pytest(output)
    assert failures == []


def test_run_tests_success(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_pass(): assert True\n")
    result = run_tests("python -m pytest test_ok.py -v", str(tmp_path))
    assert result.exit_code == 0
    assert result.failures == []


def test_run_tests_failure(tmp_path):
    (tmp_path / "test_fail.py").write_text("def test_broken(): assert False, 'oops'\n")
    result = run_tests("python -m pytest test_fail.py", str(tmp_path))
    assert result.exit_code != 0
    assert len(result.failures) > 0


def test_run_tests_timeout(tmp_path):
    (tmp_path / "test_slow.py").write_text(
        "import time\ndef test_slow(): time.sleep(60)\n"
    )
    result = run_tests("python -m pytest test_slow.py", str(tmp_path), timeout=1)
    assert result.exit_code == 1
    assert "TIMEOUT" in result.failures
