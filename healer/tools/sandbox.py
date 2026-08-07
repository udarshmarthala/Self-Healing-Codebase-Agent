from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from healer.tools.runner import RunResult, run_tests

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


def gitignored_dirs(target_repo: str) -> tuple[str, ...]:
    """Directory names the repo's own .gitignore excludes.

    A repo that ignores `fixtures/large/` or a custom venv name knows better
    than DEFAULT_IGNORES what is regenerable. Only plain directory entries are
    honoured — full gitignore semantics (globs, negation, nesting) are out of
    scope, and guessing wrong would silently omit real source files.
    """
    gitignore = Path(target_repo) / ".gitignore"
    if not gitignore.exists():
        return ()

    try:
        lines = gitignore.read_text().splitlines()
    except OSError as e:
        logger.warning("sandbox: could not read .gitignore — %s", e)
        return ()

    names: list[str] = []
    for raw in lines:
        entry = raw.strip()
        if not entry or entry.startswith(("#", "!")):
            continue
        if not entry.endswith("/"):
            continue
        name = entry.rstrip("/")
        if "/" in name or "*" in name:  # nested or glob — not a plain dir name
            continue
        names.append(name)

    if names:
        logger.info("sandbox: honouring .gitignore dirs %s", names)
    return tuple(names)


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


@contextmanager
def sandbox(
    target_repo: str,
    max_mb: int = DEFAULT_MAX_MB,
    ignores: tuple[str, ...] = DEFAULT_IGNORES,
) -> Iterator[SandboxResult]:
    """Yield an isolated copy of the repo, always cleaned up on exit.

    Declines rather than raises: if the repo is too large or the copy fails,
    the result reports `used=False` and the caller runs against the real repo.
    Isolation is an optimisation on safety, never a precondition for healing.
    """
    ignores = tuple(dict.fromkeys(ignores + gitignored_dirs(target_repo)))
    limit_bytes = max_mb * 1024 * 1024
    files, total = measure_tree(target_repo, ignores, limit_bytes=limit_bytes)

    if total > limit_bytes:
        reason = f"repo exceeds {max_mb} MB (measured at least {total / (1024 * 1024):.0f} MB)"
        logger.warning("sandbox: declined — %s", reason)
        yield SandboxResult(path=None, used=False, reason=reason)
        return

    temp_root = tempfile.mkdtemp(prefix="healer-sandbox-")
    destination = str(Path(temp_root) / Path(target_repo).name)

    try:
        copied = copy_tree(target_repo, destination, ignores)
    except OSError as e:
        shutil.rmtree(temp_root, ignore_errors=True)
        logger.error("sandbox: copy failed — %s (running in place instead)", e)
        yield SandboxResult(path=None, used=False, reason=f"copy failed: {e}")
        return

    result = SandboxResult(
        path=destination,
        used=True,
        files_copied=copied,
        bytes_copied=total,
    )
    logger.info("sandbox: %s", result.summary())

    try:
        yield result
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
        logger.debug("sandbox: removed %s", temp_root)


def rewrite_paths(text: str, sandbox_path: str, target_repo: str) -> str:
    """Map sandbox paths in test output back to the real repo.

    Without this the diagnoser reads tracebacks pointing at a temp directory
    that no longer exists, and the fixer would try to patch files there — so
    every patch would target a path outside the repo and the reviewer's scope
    guard would reject it.
    """
    if not sandbox_path:
        return text
    return text.replace(sandbox_path, target_repo)


def rewrite_failures(failures: list[str], sandbox_path: str, target_repo: str) -> list[str]:
    return [rewrite_paths(f, sandbox_path, target_repo) for f in failures]


def rewrite_command(test_command: str, target_repo: str, sandbox_path: str) -> str:
    """Point absolute references to the target repo at the sandbox instead.

    A command like `pytest /work/repo/tests` would otherwise run against the
    real repo even though cwd is the sandbox — silently defeating isolation
    while still reporting success.
    """
    if target_repo not in test_command:
        return test_command

    rewritten = test_command.replace(target_repo, sandbox_path)
    logger.info("sandbox: rewrote test command to stay inside the sandbox")
    return rewritten


def run_tests_isolated(
    test_command: str,
    target_repo: str,
    timeout: int = 300,
    max_mb: int = DEFAULT_MAX_MB,
) -> tuple[RunResult, SandboxResult]:
    """Run the suite against a throwaway copy of the repo.

    The copy includes uncommitted changes, so this verifies the patch exactly as
    applied — while leaving the real repo untouched if the suite corrupts state,
    writes stray files, or the process is killed mid-run.
    """
    with sandbox(target_repo, max_mb=max_mb) as box:
        if not box.used or box.path is None:
            result = run_tests(test_command, target_repo, timeout=timeout)
            return result, box

        command = rewrite_command(test_command, target_repo, box.path)
        result = run_tests(command, box.path, timeout=timeout)
        rewritten = RunResult(
            exit_code=result.exit_code,
            output=rewrite_paths(result.output, box.path, target_repo),
            failures=rewrite_failures(result.failures, box.path, target_repo),
        )
        logger.info(
            "sandbox: isolated run exit_code=%d failures=%d",
            rewritten.exit_code,
            len(rewritten.failures),
        )
        return rewritten, box
