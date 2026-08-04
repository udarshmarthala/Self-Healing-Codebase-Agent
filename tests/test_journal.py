import json

import pytest

from healer.state import HealerState
from healer.tools.journal import (
    MAX_EVENTS,
    SCHEMA_VERSION,
    Journal,
    JournalError,
    JournalEvent,
    check_compatible,
    ensure_git_excluded,
    exists,
    journal_path,
    load,
    save,
)


def make_state(target_repo, test_command="pytest", **kwargs):
    return HealerState(
        goal="make tests pass",
        target_repo=str(target_repo),
        test_command=test_command,
        **kwargs,
    )


def test_save_creates_journal_file(tmp_path):
    state = make_state(tmp_path)
    path = save(state.to_dict(), str(tmp_path))
    assert path == journal_path(str(tmp_path))
    assert path.exists()
    assert exists(str(tmp_path))


def test_save_load_roundtrip_preserves_state(tmp_path):
    state = make_state(tmp_path)
    state.cycle = 3
    state.record_cycle_result("output", 1, ["t.py::test_a"])
    state.record_patch("src/a.py", "diff", "fixed import")

    save(state.to_dict(), str(tmp_path))
    restored = HealerState.from_dict(load(str(tmp_path)).state)

    assert restored.cycle == 3
    assert restored.patches_applied[0]["file"] == "src/a.py"
    assert restored.failures_by_cycle[0]["failures"] == ["t.py::test_a"]


def test_roundtrip_preserves_error_fingerprints_as_a_set(tmp_path):
    state = make_state(tmp_path)
    state.is_stalled("abc123")
    save(state.to_dict(), str(tmp_path))

    restored = HealerState.from_dict(load(str(tmp_path)).state)
    assert restored.error_fingerprints == {"abc123"}


def test_save_overwrites_previous_snapshot(tmp_path):
    state = make_state(tmp_path)
    save(state.to_dict(), str(tmp_path))
    state.cycle = 7
    save(state.to_dict(), str(tmp_path))
    assert load(str(tmp_path)).last_cycle() == 7


def test_save_persists_events(tmp_path):
    events = [JournalEvent(cycle=1, kind="observe", detail="3 failures")]
    save(make_state(tmp_path).to_dict(), str(tmp_path), events=events)

    loaded = load(str(tmp_path))
    assert len(loaded.events) == 1
    assert loaded.events[0].kind == "observe"
    assert loaded.events[0].detail == "3 failures"


def test_save_leaves_no_temp_files(tmp_path):
    save(make_state(tmp_path).to_dict(), str(tmp_path))
    leftovers = list((tmp_path / ".healer").glob(".journal-*"))
    assert leftovers == []


def test_load_missing_journal_raises(tmp_path):
    with pytest.raises(JournalError, match="nothing to resume"):
        load(str(tmp_path))


def test_load_corrupt_json_raises(tmp_path):
    path = journal_path(str(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version": 1, "state": {tru')
    with pytest.raises(JournalError, match="unreadable"):
        load(str(tmp_path))


def test_load_truncated_journal_raises(tmp_path):
    """A journal cut off mid-write must be rejected, not partially trusted."""
    save(make_state(tmp_path).to_dict(), str(tmp_path))
    path = journal_path(str(tmp_path))
    full = path.read_text()
    path.write_text(full[: len(full) // 2])
    with pytest.raises(JournalError):
        load(str(tmp_path))


def test_load_version_mismatch_raises(tmp_path):
    path = journal_path(str(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1, "state": {"cycle": 1}}))
    with pytest.raises(JournalError, match="schema_version"):
        load(str(tmp_path))


def test_load_empty_state_raises(tmp_path):
    path = journal_path(str(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "state": {}}))
    with pytest.raises(JournalError, match="no state"):
        load(str(tmp_path))


def _journal(target_repo, test_command="pytest", status="in_progress"):
    return Journal(state={
        "target_repo": str(target_repo),
        "test_command": test_command,
        "status": status,
        "cycle": 2,
    })


def test_check_compatible_accepts_matching_run(tmp_path):
    check_compatible(_journal(tmp_path), str(tmp_path), "pytest")  # must not raise


def test_check_compatible_rejects_different_repo(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(JournalError, match="refusing to resume"):
        check_compatible(_journal(tmp_path), str(other), "pytest")


def test_check_compatible_rejects_different_test_command(tmp_path):
    with pytest.raises(JournalError, match="stall detection"):
        check_compatible(_journal(tmp_path), str(tmp_path), "pytest -k auth")


def test_check_compatible_rejects_finished_run(tmp_path):
    with pytest.raises(JournalError, match="nothing to resume"):
        check_compatible(_journal(tmp_path, status="success"), str(tmp_path), "pytest")


def test_check_compatible_ignores_equivalent_path_spellings(tmp_path):
    messy = f"{tmp_path}/./"
    check_compatible(_journal(tmp_path), messy, "pytest")  # must not raise


def test_ensure_git_excluded_creates_exclude_file(tmp_path):
    (tmp_path / ".git").mkdir()
    assert ensure_git_excluded(str(tmp_path))
    assert ".healer/" in (tmp_path / ".git" / "info" / "exclude").read_text()


def test_ensure_git_excluded_is_idempotent(tmp_path):
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / ".git" / "info" / "exclude").write_text("*.log\n")
    assert ensure_git_excluded(str(tmp_path))
    assert not ensure_git_excluded(str(tmp_path))
    assert (tmp_path / ".git" / "info" / "exclude").read_text().count(".healer/") == 1


def test_ensure_git_excluded_preserves_existing_entries(tmp_path):
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / ".git" / "info" / "exclude").write_text("*.log")  # no trailing newline
    ensure_git_excluded(str(tmp_path))
    lines = (tmp_path / ".git" / "info" / "exclude").read_text().splitlines()
    assert lines == ["*.log", ".healer/"]


def test_add_event_caps_the_log():
    journal = Journal()
    for n in range(MAX_EVENTS + 25):
        journal.add_event(cycle=n, kind="observe", detail=f"event {n}")
    assert len(journal.events) == MAX_EVENTS
    assert journal.events[-1].detail == f"event {MAX_EVENTS + 24}"
    assert journal.events[0].detail == "event 25"  # oldest dropped


def test_events_for_filters_by_cycle():
    journal = Journal()
    journal.add_event(1, "observe", "a")
    journal.add_event(2, "patch", "b")
    assert [e.detail for e in journal.events_for(2)] == ["b"]


def test_timeline_renders_tail_with_elision():
    journal = Journal()
    for n in range(30):
        journal.add_event(cycle=n, kind="observe", detail=f"e{n}")
    text = journal.timeline(limit=5)
    assert "25 earlier event(s)" in text
    assert "e29" in text
    assert "e0" not in text


def test_timeline_empty():
    assert Journal().timeline() == "(no events recorded)"
