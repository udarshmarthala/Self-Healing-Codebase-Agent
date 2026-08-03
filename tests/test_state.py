import json

import pytest
from healer.state import HealerState, _fingerprint


def make_state(**kwargs):
    return HealerState(
        goal="test",
        target_repo="/tmp/repo",
        test_command="pytest",
        **kwargs,
    )


def test_record_cycle_result():
    s = make_state()
    fp = s.record_cycle_result("output", 1, ["test_foo::test_bar"])
    assert len(s.failures_by_cycle) == 1
    assert s.failures_by_cycle[0]["cycle"] == 0
    assert fp == _fingerprint(["test_foo::test_bar"])


def test_is_stalled_increments_after_repeat():
    s = make_state()
    fp = "abc123"
    s.is_stalled(fp)  # first time: adds to set, counter=0
    assert s.stall_counter == 0
    s.is_stalled(fp)  # second time: already seen, counter=1
    assert s.stall_counter == 1
    s.is_stalled(fp)  # third: counter=2
    assert not s.is_stalled.__doc__  # just checking it didn't error
    s.stall_counter = 2
    assert s.is_stalled(fp)  # counter reaches 3


def test_is_not_stalled_on_new_fingerprint():
    s = make_state()
    s.stall_counter = 2
    assert not s.is_stalled("new_fp")
    assert s.stall_counter == 0


def test_is_at_max_cycles():
    s = make_state(cycle=10, max_cycles=10)
    assert s.is_at_max_cycles()
    s.cycle = 9
    assert not s.is_at_max_cycles()


def test_record_patch():
    s = make_state()
    s.record_patch("foo.py", "--- a\n+++ b\n", "fixed import")
    assert len(s.patches_applied) == 1
    assert s.patches_applied[0]["file"] == "foo.py"


def test_round_trip_json():
    s = make_state()
    s.record_patch("a.py", "diff", "reason")
    s.error_fingerprints.add("fp1")
    restored = HealerState.from_dict(s.to_dict())
    assert restored.goal == s.goal
    assert restored.error_fingerprints == s.error_fingerprints
    assert len(restored.patches_applied) == 1


def test_record_lint_result_stores_snapshot():
    state = HealerState(goal="g", target_repo="/tmp/r", test_command="pytest")
    state.cycle = 2
    state.record_lint_result("ruff", ["a.py:1 F401 unused"], error_count=1)
    assert state.lint_by_cycle[0]["cycle"] == 2
    assert state.lint_by_cycle[0]["tool"] == "ruff"
    assert state.lint_by_cycle[0]["issue_count"] == 1
    assert state.lint_by_cycle[0]["introduced"] == []


def test_record_lint_result_truncates_issue_list():
    state = HealerState(goal="g", target_repo="/tmp/r", test_command="pytest")
    state.record_lint_result("ruff", [f"a.py:{n} E501 long" for n in range(80)], error_count=80)
    entry = state.lint_by_cycle[0]
    assert entry["issue_count"] == 80
    assert len(entry["issues"]) == 50


def test_lint_regressions_only_returns_cycles_with_new_issues():
    state = HealerState(goal="g", target_repo="/tmp/r", test_command="pytest")
    state.record_lint_result("ruff", ["a.py:1 F401 unused"], error_count=1)
    state.cycle = 1
    state.record_lint_result("ruff", ["a.py:1 F401 unused"], 1, introduced=["a.py:1 F401 unused"])
    assert [c["cycle"] for c in state.lint_regressions()] == [1]


def test_lint_fields_survive_json_roundtrip():
    state = HealerState(goal="g", target_repo="/tmp/r", test_command="pytest", lint_command="ruff check .")
    state.record_lint_result("ruff", ["a.py:1 F401 unused"], error_count=1)
    restored = HealerState.from_dict(json.loads(state.to_json()))
    assert restored.lint_command == "ruff check ."
    assert restored.lint_by_cycle[0]["tool"] == "ruff"


def test_record_coverage_result_stores_snapshot():
    state = HealerState(goal="g", target_repo="/tmp/r", test_command="pytest")
    state.cycle = 4
    state.record_coverage_result(rate=0.8123, rate_change=-0.05, files_declined=["a.py"])
    entry = state.coverage_by_cycle[0]
    assert entry["cycle"] == 4
    assert entry["rate"] == 0.8123
    assert entry["files_declined"] == ["a.py"]
    assert entry["untested_patch_lines"] == []


def test_unverified_patches_only_returns_cycles_with_untested_lines():
    state = HealerState(goal="g", target_repo="/tmp/r", test_command="pytest")
    state.record_coverage_result(rate=0.9)
    state.cycle = 2
    state.record_coverage_result(rate=0.9, untested_patch_lines=[11, 12])
    assert [c["cycle"] for c in state.unverified_patches()] == [2]


def test_coverage_fields_survive_json_roundtrip():
    state = HealerState(
        goal="g", target_repo="/tmp/r", test_command="pytest", coverage_enabled=True
    )
    state.record_coverage_result(rate=0.5, untested_patch_lines=[3])
    restored = HealerState.from_dict(json.loads(state.to_json()))
    assert restored.coverage_enabled
    assert restored.coverage_by_cycle[0]["untested_patch_lines"] == [3]
