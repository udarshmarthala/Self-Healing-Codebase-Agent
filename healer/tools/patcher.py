from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class PatchError(Exception):
    pass


def apply_patch(repo: str, unified_diff: str) -> None:
    """Apply a unified diff to the repo. Raises PatchError on failure."""
    logger.info("patcher: applying patch (%d chars)", len(unified_diff))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(unified_diff)
        patch_file = f.name

    try:
        proc = subprocess.run(
            ["patch", "-p1", "--input", patch_file],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        output = proc.stdout + proc.stderr
        logger.debug("patcher output: %s", output)
        if proc.returncode != 0:
            raise PatchError(f"patch failed (exit {proc.returncode}): {output}")
        logger.info("patcher: patch applied successfully")
    finally:
        Path(patch_file).unlink(missing_ok=True)


def write_file(repo: str, relative_path: str, content: str) -> None:
    """Write full file content (used when diff isn't appropriate)."""
    target = Path(repo) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_text() if target.exists() else None
    try:
        target.write_text(content)
        logger.info("patcher: wrote %s (%d chars)", relative_path, len(content))
    except Exception as exc:
        if original is not None:
            target.write_text(original)
        raise PatchError(f"write_file failed for {relative_path}: {exc}") from exc


def verify_patch_applies(repo: str, unified_diff: str) -> bool:
    """Dry-run check — does not modify files."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(unified_diff)
        patch_file = f.name
    try:
        proc = subprocess.run(
            ["patch", "-p1", "--dry-run", "--input", patch_file],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    finally:
        Path(patch_file).unlink(missing_ok=True)
