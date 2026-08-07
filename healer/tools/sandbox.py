from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Directories never worth copying: either regenerable, enormous, or healer's own
# scaffolding. Copying node_modules alone can turn a 2s sandbox into a 2min one.
DEFAULT_IGNORES: tuple[str, ...] = (
    ".git",
    ".healer",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    ".coverage",
    "htmlcov",
    "dist",
    "build",
)

# Above this the copy costs more than the isolation is worth, and the loop
# degrades to running in place rather than stalling for minutes per cycle.
DEFAULT_MAX_MB = 500


@dataclass
class SandboxResult:
    """Outcome of preparing a sandbox — never an exception, always a decision."""

    path: str | None  # None when the sandbox was declined
    used: bool
    reason: str = ""
    files_copied: int = 0
    bytes_copied: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def megabytes(self) -> float:
        return self.bytes_copied / (1024 * 1024)

    def summary(self) -> str:
        if not self.used:
            return f"sandbox: not used ({self.reason})"
        return (
            f"sandbox: {self.files_copied} file(s), "
            f"{self.megabytes:.1f} MB at {self.path}"
        )
