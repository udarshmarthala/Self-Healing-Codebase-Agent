from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

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


def is_ignored(name: str, ignores: tuple[str, ...] = DEFAULT_IGNORES) -> bool:
    return name in ignores


def measure_tree(
    source: str,
    ignores: tuple[str, ...] = DEFAULT_IGNORES,
    limit_bytes: int | None = None,
) -> tuple[int, int]:
    """Count files and bytes that a copy would move, skipping ignored dirs.

    Stops early once `limit_bytes` is exceeded — the caller only needs to know
    the repo is too big, and walking a huge tree twice defeats the purpose.
    """
    files = 0
    total = 0

    for root, dirnames, filenames in os.walk(source):
        dirnames[:] = [d for d in dirnames if not is_ignored(d, ignores)]
        for name in filenames:
            path = Path(root) / name
            if path.is_symlink():
                files += 1
                continue
            try:
                total += path.stat().st_size
            except OSError:  # vanished mid-walk, or unreadable
                continue
            files += 1
            if limit_bytes is not None and total > limit_bytes:
                return files, total

    return files, total


def copy_tree(source: str, destination: str, ignores: tuple[str, ...] = DEFAULT_IGNORES) -> int:
    """Copy a working tree into an empty destination. Returns files copied.

    Symlinks are recreated as symlinks rather than followed: following them
    could pull in gigabytes from outside the repo, or escape it entirely.
    """
    copied = 0

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        nonlocal copied
        skipped = {n for n in names if is_ignored(n, ignores)}
        copied += len(names) - len(skipped)
        return skipped

    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=_ignore,
        dirs_exist_ok=True,
    )
    return copied
