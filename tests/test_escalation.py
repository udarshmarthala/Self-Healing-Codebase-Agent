from healer.escalation import generate_report
from healer.state import HealerState


def make_state(status="escalated", cycle=8):
    s = HealerState(
        goal="Make tests pass",
        target_repo="/tmp/repo",
        test_command="pytest",
        cycle=cycle,
        max_cycles=10,
        status=status,
    )
    s.failures_by_cycle.append({
        "cycle": cycle,
        "exit_code": 1,
        "failures": ["test_auth.py::test_login", "test_auth.py::test_logout"],
        "fingerprint": "abc123",
        "output_snippet": "FAILED test_auth.py::test_login",
    })
    s.patches_applied.append({
        "cycle": 4,
        "file": "auth.py",
        "diff": "--- a\n+++ b",
        "rationale": "fixed token expiry",
    })
    return s


def test_report_contains_target():
    s = make_state()
    report = generate_report(s, "stall")
    assert "/tmp/repo" in report


def test_report_contains_exit_reason():
    s = make_state()
    report = generate_report(s, "max_cycles (10) reached")
    assert "max_cycles" in report


def test_report_contains_failing_tests():
    s = make_state()
    report = generate_report(s, "stall")
    assert "test_auth.py::test_login" in report


def test_report_contains_what_was_tried():
    s = make_state()
    report = generate_report(s, "stall")
    assert "auth.py" in report
    assert "fixed token expiry" in report


def test_report_markdown_structure():
    s = make_state()
    report = generate_report(s, "stall")
    assert "# Healer Escalation Report" in report
    assert "## Failing Tests at Exit" in report
    assert "## What Was Tried" in report
    assert "## Root Cause Hypothesis" in report
    assert "## Recommended Human Action" in report


def test_report_omits_lint_section_when_disabled():
    state = HealerState(goal="g", target_repo="/tmp/r", test_command="pytest")
    state.record_cycle_result("out", 1, ["t.py::test_a"])
    assert "Static Analysis at Exit" not in generate_report(state, "max_cycles")


def test_report_includes_lint_section():
    state = HealerState(
        goal="g", target_repo="/tmp/r", test_command="pytest", lint_command="ruff check ."
    )
    state.record_cycle_result("out", 1, ["t.py::test_a"])
    state.record_lint_result("ruff", ["src/a.py:1 F401 unused"], error_count=1)
    report = generate_report(state, "stall")
    assert "## Static Analysis at Exit" in report
    assert "ruff check ." in report
    assert "src/a.py:1 F401 unused" in report


def test_report_lists_lint_regressions():
    state = HealerState(
        goal="g", target_repo="/tmp/r", test_command="pytest", lint_command="ruff check ."
    )
    state.record_cycle_result("out", 1, ["t.py::test_a"])
    state.cycle = 3
    state.record_lint_result(
        "ruff", ["src/a.py:9 F821 undefined name"], 1, introduced=["src/a.py:9 F821 undefined name"]
    )
    report = generate_report(state, "stall")
    assert "rolled back for introducing lint errors" in report
    assert "Cycle 3: src/a.py:9 F821 undefined name" in report
