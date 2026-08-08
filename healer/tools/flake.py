from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Re-runs used to tell a flaky test from a genuinely failing one. Three is the
# smallest number that can show a mixed result without doubling cycle time.
DEFAULT_RETRIES = 3


@dataclass
class FlakeVerdict:
    """How one failing test behaved across repeated runs of unchanged code."""

    test_id: str
    runs: int
    passes: int

    @property
    def failures(self) -> int:
        return self.runs - self.passes

    @property
    def is_flaky(self) -> bool:
        """Mixed results with nothing changed in between. The code cannot be
        both broken and fine, so the test itself is nondeterministic."""
        return self.runs > 1 and 0 < self.passes < self.runs

    @property
    def is_consistent_failure(self) -> bool:
        return self.runs > 0 and self.passes == 0

    def __str__(self) -> str:
        if self.is_flaky:
            return f"{self.test_id}: FLAKY ({self.passes}/{self.runs} passed)"
        if self.is_consistent_failure:
            return f"{self.test_id}: consistent failure (0/{self.runs} passed)"
        return f"{self.test_id}: passed on re-run ({self.passes}/{self.runs})"


@dataclass
class FlakeReport:
    verdicts: list[FlakeVerdict] = field(default_factory=list)
    checked: bool = True  # False when flake detection could not run
    reason: str = ""

    @property
    def flaky(self) -> list[str]:
        return [v.test_id for v in self.verdicts if v.is_flaky]

    @property
    def real_failures(self) -> list[str]:
        return [v.test_id for v in self.verdicts if v.is_consistent_failure]

    def summary(self) -> str:
        if not self.checked:
            return f"flake: not checked ({self.reason})"
        if not self.verdicts:
            return "flake: nothing to check"
        return f"flake: {len(self.flaky)} flaky, {len(self.real_failures)} consistent"
