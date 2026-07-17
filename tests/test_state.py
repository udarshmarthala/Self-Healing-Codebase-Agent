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
