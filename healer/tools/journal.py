from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
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


def journal_path(target_repo: str) -> str:
    """Journal location for a target repo: <target>/.healer/journal.json."""
    return str(Path(target_repo) / JOURNAL_DIRNAME / JOURNAL_FILENAME)


def _atomic_write(path: str, text: str) -> None:
    """Write via a temp file in the same directory + os.replace.

    A journal truncated by a crash mid-write is worse than a missing one,
    because --resume would read it and trust it. os.replace is atomic on the
    same filesystem, so readers see either the old file or the complete new one.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=str(destination.parent), prefix=".journal-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, destination)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def save(state_dict: dict[str, Any], path: str, events: list[JournalEvent] | None = None) -> None:
    """Persist a state snapshot. Never raises on write failure — losing the
    journal must not kill an otherwise healthy run, but it is always logged."""
    journal = Journal(version=SCHEMA_VERSION, state=state_dict, events=events or [])
    try:
        _atomic_write(path, json.dumps(journal.to_dict(), indent=2))
        logger.info("journal: saved cycle %d to %s", journal.last_cycle, path)
    except OSError as e:
        logger.error("journal: could not write %s — %s (run continues)", path, e)


def load(path: str) -> Journal:
    """Read a journal. Raises JournalError on anything suspect — callers that
    resume must not proceed on a journal they cannot fully trust."""
    file = Path(path)
    if not file.exists():
        raise JournalError(f"no journal at {path}")

    try:
        raw = file.read_text()
    except OSError as e:
        raise JournalError(f"could not read {path}: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise JournalError(f"journal at {path} is corrupt or truncated: {e}") from e

    if not isinstance(data, dict):
        raise JournalError(f"journal at {path} is not a JSON object")

    journal = Journal.from_dict(data)

    if journal.version != SCHEMA_VERSION:
        raise JournalError(
            f"journal at {path} has schema version {journal.version}, "
            f"this healer writes version {SCHEMA_VERSION}"
        )
    if not journal.state:
        raise JournalError(f"journal at {path} carries no state")

    logger.info("journal: loaded cycle %d from %s", journal.last_cycle, path)
    return journal
