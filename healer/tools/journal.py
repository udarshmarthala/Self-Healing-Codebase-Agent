from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
