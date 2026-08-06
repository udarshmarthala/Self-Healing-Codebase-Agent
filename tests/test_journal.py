import json

import pytest

from healer.state import HealerState
from healer.tools.journal import (
    SCHEMA_VERSION,
    Journal,
    JournalError,
    JournalEvent,
    Recorder,
    assert_compatible,
    ensure_git_excluded,
    is_exhausted,
    journal_path,
    load,
    save,
)


def make_state(**kwargs):
    defaults = dict(goal="fix tests", target_repo="/tmp/repo", test_command="pytest")
    defaults.update(kwargs)
    return HealerState(**defaults)


def test_journal_path_is_under_healer_dir():
    assert journal_path("/tmp/repo").endswith("/tmp/repo/.healer/journal.json")


def test_save_load_roundtrip(tmp_path):
    path = str(tmp_path / ".healer" / "journal.json")
    state = make_state()
    state.cycle = 3
    state.record_cycle_result("out", 1, ["t.py::test_a"])

    save(state.to_dict(), path, [JournalEvent(3, "observe", "1 failure")])
    journal = load(path)

    assert journal.version == SCHEMA_VERSION
    assert journal.last_cycle == 3
    assert journal.state["test_command"] == "pytest"
    assert journal.events[0].phase == "observe"


def test_saved_state_restores_into_healer_state(tmp_path):
    path = str(tmp_path / "journal.json")
    state = make_state()
    state.cycle = 2
    state.record_cycle_result("out", 1, ["t.py::test_a"])
    state.record_patch("a.py", "diff", "why")

    save(state.to_dict(), path)
    restored = HealerState.from_dict(load(path).state)

    assert restored.cycle == 2
    assert restored.patches_applied[0]["file"] == "a.py"
    assert restored.error_fingerprints == state.error_fingerprints


def test_save_creates_missing_directories(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "journal.json")
    save(make_state().to_dict(), path)
    assert load(path).state["goal"] == "fix tests"


def test_save_leaves_no_temp_files(tmp_path):
    path = str(tmp_path / "journal.json")
    save(make_state().to_dict(), path)
    assert [p.name for p in tmp_path.iterdir()] == ["journal.json"]


def test_save_overwrites_previous_journal(tmp_path):
    path = str(tmp_path / "journal.json")
    first = make_state()
    first.cycle = 1
    save(first.to_dict(), path)

    second = make_state()
    second.cycle = 7
    save(second.to_dict(), path)

    assert load(path).last_cycle == 7


def test_load_missing_journal_raises(tmp_path):
    with pytest.raises(JournalError, match="no journal"):
        load(str(tmp_path / "absent.json"))


def test_load_truncated_journal_raises(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text('{"version": 1, "state": {"cycle": 2')
    with pytest.raises(JournalError, match="corrupt or truncated"):
        load(str(path))


def test_load_wrong_schema_version_raises(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text(json.dumps({"version": SCHEMA_VERSION + 1, "state": {"cycle": 1}}))
    with pytest.raises(JournalError, match="schema version"):
        load(str(path))


def test_load_stateless_journal_raises(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text(json.dumps({"version": SCHEMA_VERSION, "state": {}}))
    with pytest.raises(JournalError, match="no state"):
        load(str(path))


def test_load_non_object_raises(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(JournalError, match="not a JSON object"):
        load(str(path))


def test_save_failure_does_not_raise(tmp_path):
    # A directory where the journal file should be makes the write fail.
    blocked = tmp_path / "journal.json"
    blocked.mkdir()
    save(make_state().to_dict(), str(blocked))  # logged, not raised


def _journal(**state):
    base = {"target_repo": "/tmp/repo", "test_command": "pytest", "max_cycles": 10, "cycle": 3}
    base.update(state)
    return Journal(version=SCHEMA_VERSION, state=base)


def test_assert_compatible_accepts_matching_run():
    assert_compatible(_journal(), "/tmp/repo", "pytest")


def test_assert_compatible_rejects_different_repo():
    with pytest.raises(JournalError, match="target repo"):
        assert_compatible(_journal(), "/tmp/other", "pytest")


def test_assert_compatible_rejects_different_test_command():
    with pytest.raises(JournalError, match="test command"):
        assert_compatible(_journal(), "/tmp/repo", "pytest -x")


def test_assert_compatible_rejects_finished_run():
    with pytest.raises(JournalError, match="finished run"):
        assert_compatible(_journal(status="success"), "/tmp/repo", "pytest")


def test_is_exhausted_respects_max_cycles():
    assert is_exhausted(_journal(cycle=10, max_cycles=10))
    assert not is_exhausted(_journal(cycle=9, max_cycles=10))


def test_ensure_git_excluded_appends_entry(tmp_path):
    info = tmp_path / ".git" / "info"
    info.mkdir(parents=True)
    ensure_git_excluded(str(tmp_path))
    assert ".healer/" in (info / "exclude").read_text().splitlines()


def test_ensure_git_excluded_is_idempotent(tmp_path):
    info = tmp_path / ".git" / "info"
    info.mkdir(parents=True)
    (info / "exclude").write_text("*.log\n")
    ensure_git_excluded(str(tmp_path))
    ensure_git_excluded(str(tmp_path))
    content = (info / "exclude").read_text()
    assert content.count(".healer/") == 1
    assert "*.log" in content


def test_ensure_git_excluded_handles_missing_newline(tmp_path):
    info = tmp_path / ".git" / "info"
    info.mkdir(parents=True)
    (info / "exclude").write_text("*.log")  # no trailing newline
    ensure_git_excluded(str(tmp_path))
    assert (info / "exclude").read_text().splitlines() == ["*.log", ".healer/"]


def test_ensure_git_excluded_without_git_dir_is_noop(tmp_path):
    ensure_git_excluded(str(tmp_path))  # must not raise


def test_recorder_checkpoints_state_and_events(tmp_path):
    path = str(tmp_path / "journal.json")
    recorder = Recorder(path)
    state = make_state()
    state.cycle = 1

    recorder.record(1, "observe", "2 failures")
    recorder.record(1, "verify", "1 failure")
    recorder.checkpoint(state.to_dict())

    journal = load(path)
    assert [e.phase for e in journal.events] == ["observe", "verify"]
    assert journal.last_cycle == 1


def test_recorder_caps_event_count(tmp_path):
    recorder = Recorder(str(tmp_path / "journal.json"))
    for n in range(250):
        recorder.record(n, "observe", f"event {n}")
    assert len(recorder.events) == 200
    assert recorder.events[-1].detail == "event 249"


def test_recorder_adopt_prepends_resumed_events(tmp_path):
    recorder = Recorder(str(tmp_path / "journal.json"))
    recorder.record(4, "observe", "new")
    recorder.adopt([JournalEvent(1, "observe", "old")])
    assert [e.detail for e in recorder.events] == ["old", "new"]


def test_recorder_trail_renders_recent_events(tmp_path):
    recorder = Recorder(str(tmp_path / "journal.json"))
    assert "no events" in recorder.trail()
    recorder.record(2, "act", "patched a.py")
    assert "cycle 2 [act] patched a.py" in recorder.trail()
