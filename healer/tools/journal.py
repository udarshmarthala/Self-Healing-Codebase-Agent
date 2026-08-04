from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Bumped whenever the on-disk shape changes incompatibly. A journal written by
# a newer healer must never be silently half-read by an older one.
SCHEMA_VERSION = 1

JOURNAL_DIR = ".healer"
JOURNAL_FILENAME = "journal.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def last_cycle(self) -> int:
        return int(self.state.get("cycle", 0))

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
