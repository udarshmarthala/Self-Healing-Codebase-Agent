"""Integration test: healer loop against a real broken mini-repo."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from healer.loop import run
from healer.state import HealerState


@pytest.fixture
def broken_repo(tmp_path):
    """Mini repo with one broken test and one passing test."""
    (tmp_path / "math_utils.py").write_text(textwrap.dedent("""
        def add(a, b):
            return a - b  # bug: should be +
    """))
    (tmp_path / "test_math.py").write_text(textwrap.dedent("""
        from math_utils import add

        def test_add():
            assert add(2, 3) == 5

        def test_add_zero():
            assert add(0, 0) == 0
    """))
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


def _make_state(repo: Path, max_cycles: int = 3) -> HealerState:
    return HealerState(
        goal="Fix math_utils",
        target_repo=str(repo),
        test_command="python -m pytest -v",
        max_cycles=max_cycles,
    )


def test_loop_detects_failures(broken_repo):
    """Loop should observe failures on cycle 1 without touching LLM."""
    from healer.tools.runner import run_tests
    result = run_tests("python -m pytest -v", str(broken_repo))
    assert result.exit_code != 0
    assert len(result.failures) > 0


def test_loop_escalates_at_max_cycles(broken_repo):
    """With mocked agents that never fix, loop must escalate at max_cycles."""
    from healer.agents.fixer import Patch
    from healer.agents.reviewer import ReviewResult

    mock_diagnosis = MagicMock()
    mock_diagnosis.affected_files = ["math_utils.py"]
    mock_diagnosis.error_type = "assertion_error"
    mock_diagnosis.suggested_strategy = "fix_logic"
    mock_diagnosis.root_cause = "subtraction instead of addition"
    mock_diagnosis.failing_tests = ["test_math.py::test_add"]
    mock_diagnosis.raw_errors = ["AssertionError: assert -1 == 5"]

    mock_patch = Patch(
        file_path="math_utils.py",
        unified_diff="",
        rationale="attempted fix",
        full_content="def add(a, b):\n    return a - b\n",  # still broken
    )

    with (
        patch("healer.loop.diagnose", return_value=mock_diagnosis),
        patch("healer.loop.generate_fix", return_value=mock_patch),
        patch(
            "healer.loop.review_patch",
            return_value=ReviewResult(approved=True, reason="ok", score=8),
        ),
    ):
        state = run(_make_state(broken_repo, max_cycles=2))

    assert state.status == "escalated"
    report = (broken_repo / "HEALER_ESCALATION.md").read_text()
    assert "Healer Escalation Report" in report


def test_loop_succeeds_when_fix_applied(broken_repo):
    """When fixer provides the correct fix, loop exits with success."""
    from healer.agents.fixer import Patch
    from healer.agents.reviewer import ReviewResult

    mock_diagnosis = MagicMock()
    mock_diagnosis.affected_files = ["math_utils.py"]
    mock_diagnosis.error_type = "assertion_error"
    mock_diagnosis.suggested_strategy = "fix_logic"
    mock_diagnosis.root_cause = "subtraction instead of addition"
    mock_diagnosis.failing_tests = ["test_math.py::test_add"]
    mock_diagnosis.raw_errors = []

    correct_fix = Patch(
        file_path="math_utils.py",
        unified_diff="",
        rationale="fixed + vs -",
        full_content="def add(a, b):\n    return a + b\n",
    )

    with (
        patch("healer.loop.diagnose", return_value=mock_diagnosis),
        patch("healer.loop.generate_fix", return_value=correct_fix),
        patch(
            "healer.loop.review_patch",
            return_value=ReviewResult(approved=True, reason="correct fix", score=9),
        ),
    ):
        state = run(_make_state(broken_repo, max_cycles=5))

    assert state.status == "success"
