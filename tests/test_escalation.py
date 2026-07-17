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
