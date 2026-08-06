from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Bumped whenever the on-disk shape changes incompatibly. A journal written by
# a newer healer must never be silently reinterpreted by an older one.
SCHEMA_VERSION = 1

JOURNAL_DIRNAME = ".healer"
JOURNAL_FILENAME = "journal.json"


class JournalError(Exception):
    """Raised when a journal cannot be read, or does not match the current run.

    Unlike the other tools, the journal does not degrade silently: resuming
    from a journal we cannot fully trust is worse than not resuming at all.
    """


@dataclass
class JournalEvent:
    """One phase boundary in a cycle — the loop's audit trail."""

    cycle: int
    phase: str  # observe | reason | act | verify | exit
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"cycle": self.cycle, "phase": self.phase, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JournalEvent:
        return cls(
            cycle=int(d.get("cycle", 0)),
            phase=str(d.get("phase", "unknown")),
            detail=str(d.get("detail", "")),
        )

    def __str__(self) -> str:
        return f"cycle {self.cycle} [{self.phase}] {self.detail}"


@dataclass
class Journal:
    """A durable snapshot of a run: the full state plus a phase-event trail."""

    version: int = SCHEMA_VERSION
    state: dict[str, Any] = field(default_factory=dict)
    events: list[JournalEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "state": self.state,
            "events": [e.to_dict() for e in self.events],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Journal:
        return cls(
            version=int(d.get("version", 0)),
            state=d.get("state") or {},
            events=[JournalEvent.from_dict(e) for e in d.get("events", [])],
        )

    @property
    def last_cycle(self) -> int:
        return int(self.state.get("cycle", 0))
