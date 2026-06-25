from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _run(args: list[str], cwd: str) -> tuple[int, str]:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    logger.debug("git %s → %d", " ".join(args[1:]), proc.returncode)
    return proc.returncode, output


def commit(repo: str, message: str) -> bool:
    _run(["git", "add", "-A"], repo)
    code, out = _run(["git", "commit", "-m", message], repo)
    if code != 0:
        logger.warning("git commit failed: %s", out)
        return False
    logger.info("git commit ok: %s", message)
    return True


def diff(repo: str, ref: str = "HEAD") -> str:
    _, out = _run(["git", "diff", ref], repo)
    return out


def current_sha(repo: str) -> str:
    _, out = _run(["git", "rev-parse", "HEAD"], repo)
    return out.strip()


def rollback_to(repo: str, sha: str) -> bool:
    code, out = _run(["git", "reset", "--hard", sha], repo)
    if code != 0:
        logger.error("git rollback to %s failed: %s", sha, out)
        return False
    logger.info("git rolled back to %s", sha)
    return True


def is_git_repo(path: str) -> bool:
    return Path(path, ".git").exists()


def ensure_git_repo(path: str) -> None:
    if not is_git_repo(path):
        code, out = _run(["git", "init"], path)
        if code != 0:
            raise RuntimeError(f"git init failed in {path}: {out}")
        _run(["git", "add", "-A"], path)
        _run(["git", "commit", "-m", "init: healer baseline"], path)
        logger.info("git: initialized new repo at %s", path)
