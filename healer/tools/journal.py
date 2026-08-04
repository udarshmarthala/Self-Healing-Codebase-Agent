from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Bumped whenever the on-disk shape changes incompatibly. A journal written by
# a newer healer must never be silently half-read by an older one.
SCHEMA_VERSION = 1

JOURNAL_DIR = ".healer"
JOURNAL_FILENAME = "journal.json"

# A long run with a chatty diagnoser can produce a lot of events; keep the
# journal bounded so it stays readable and cheap to rewrite every cycle.
MAX_EVENTS = 500


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class JournalEvent:
    """One thing that happened during a run, in order."""

    cycle: int
    kind: str  # observe | patch | verify | rollback | escalate | resume
    detail: str
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "cycle": self.cycle,
            "kind": self.kind,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> JournalEvent:
        return cls(
            cycle=int(d.get("cycle", 0)),
            kind=str(d.get("kind", "unknown")),
            detail=str(d.get("detail", "")),
            timestamp=str(d.get("timestamp", "")),
        )

    def __str__(self) -> str:
        return f"[cycle {self.cycle}] {self.kind}: {self.detail}"


class JournalError(Exception):
    """Raised when a journal cannot be trusted — never swallowed silently."""


@dataclass
class Journal:
    """A run's durable record: the full state plus an ordered event log."""

    state: dict = field(default_factory=dict)
    events: list[JournalEvent] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    updated_at: str = field(default_factory=_now)

    def add_event(self, cycle: int, kind: str, detail: str) -> None:
        self.events.append(JournalEvent(cycle=cycle, kind=kind, detail=detail))
        if len(self.events) > MAX_EVENTS:
            # Drop from the front: the oldest cycles are the least useful when
            # diagnosing why a run stalled or was interrupted.
            self.events = self.events[-MAX_EVENTS:]

    def last_cycle(self) -> int:
        return int(self.state.get("cycle", 0))

    def events_for(self, cycle: int) -> list[JournalEvent]:
        return [e for e in self.events if e.cycle == cycle]

    def timeline(self, limit: int = 20) -> str:
        """Human-readable tail of the event log, for reports and --resume output."""
        if not self.events:
            return "(no events recorded)"
        shown = self.events[-limit:]
        lines = [str(e) for e in shown]
        if len(self.events) > limit:
            lines.insert(0, f"...{len(self.events) - limit} earlier event(s)")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "updated_at": self.updated_at,
            "state": self.state,
            "events": [e.to_dict() for e in self.events],
        }


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then os.replace.

    The healer is killed mid-run often enough (Ctrl-C, timeout, crash) that a
    partially written journal is a real failure mode — and a truncated journal
    is worse than none, because --resume would trust it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".journal-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def journal_path(target_repo: str) -> Path:
    return Path(target_repo) / JOURNAL_DIR / JOURNAL_FILENAME


def save(state_dict: dict, target_repo: str, events: list[JournalEvent] | None = None) -> Path:
    """Persist a state snapshot. Returns the path written.

    Failures are logged and re-raised as JournalError: losing the journal
    silently would make --resume quietly wrong, which is worse than stopping.
    """
    path = journal_path(target_repo)
    journal = Journal(state=state_dict, events=list(events or []))

    try:
        _atomic_write(path, json.dumps(journal.to_dict(), indent=2))
    except (OSError, TypeError) as e:
        logger.error("journal: failed to write %s — %s", path, e)
        raise JournalError(f"could not write journal at {path}: {e}") from e

    logger.info(
        "journal: saved cycle %d to %s (%d event(s))",
        journal.last_cycle(),
        path,
        len(journal.events),
    )
    return path


def exists(target_repo: str) -> bool:
    return journal_path(target_repo).exists()


def load(target_repo: str) -> Journal:
    """Read and validate a journal. Raises JournalError if it cannot be trusted."""
    path = journal_path(target_repo)

    if not path.exists():
        raise JournalError(f"no journal at {path} — nothing to resume")

    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.error("journal: unreadable journal at %s — %s", path, e)
        raise JournalError(f"journal at {path} is unreadable: {e}") from e

    if not isinstance(raw, dict):
        raise JournalError(f"journal at {path} is not an object")

    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise JournalError(
            f"journal at {path} has schema_version {version!r}, expected {SCHEMA_VERSION} — "
            "delete it to start a fresh run"
        )

    state = raw.get("state")
    if not isinstance(state, dict) or not state:
        raise JournalError(f"journal at {path} carries no state")

    journal = Journal(
        state=state,
        events=[JournalEvent.from_dict(e) for e in raw.get("events", []) if isinstance(e, dict)],
        schema_version=version,
        updated_at=str(raw.get("updated_at", "")),
    )
    logger.info(
        "journal: loaded cycle %d from %s (%d event(s))",
        journal.last_cycle(),
        path,
        len(journal.events),
    )
    return journal


def check_compatible(journal: Journal, target_repo: str, test_command: str) -> None:
    """Refuse to resume a journal that describes a different run.

    Resuming with a different test command or repo would carry over failure
    fingerprints and a stall counter that describe work that never happened,
    corrupting stall detection for the rest of the run.
    """
    saved_repo = str(journal.state.get("target_repo", ""))
    saved_cmd = str(journal.state.get("test_command", ""))

    if saved_repo and Path(saved_repo).resolve() != Path(target_repo).resolve():
        raise JournalError(
            f"journal was recorded for {saved_repo}, not {target_repo} — refusing to resume"
        )

    if saved_cmd and saved_cmd != test_command:
        raise JournalError(
            f"journal was recorded with test command {saved_cmd!r}, not {test_command!r} — "
            "stall detection would carry over fingerprints from a different suite"
        )

    status = journal.state.get("status")
    if status in ("success", "escalated"):
        raise JournalError(f"journal already finished with status {status!r} — nothing to resume")


_EXCLUDE_LINE = f"{JOURNAL_DIR}/"


def ensure_git_excluded(target_repo: str) -> bool:
    """Add the journal directory to .git/info/exclude.

    git.commit() stages with `git add -A`, so without this the healer's own
    journal lands in the user's commits. info/exclude is used rather than
    .gitignore because it is local-only — the healer must not modify a tracked
    file in the repo it is healing.
    """
    exclude_file = Path(target_repo) / ".git" / "info" / "exclude"

    try:
        if exclude_file.exists():
            current = exclude_file.read_text()
            if _EXCLUDE_LINE in current.splitlines():
                return False
            separator = "" if current.endswith("\n") or not current else "\n"
            exclude_file.write_text(f"{current}{separator}{_EXCLUDE_LINE}\n")
        else:
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            exclude_file.write_text(f"{_EXCLUDE_LINE}\n")
    except OSError as e:
        logger.warning("journal: could not update %s — %s", exclude_file, e)
        return False

    logger.info("journal: excluded %s from git in %s", _EXCLUDE_LINE, target_repo)
    return True
