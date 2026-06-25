from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    exit_code: int
    output: str  # combined stdout + stderr
    failures: list[str]


def run_tests(test_command: str, target_repo: str, timeout: int = 300) -> RunResult:
    logger.info("runner: running %r in %s", test_command, target_repo)
    try:
        proc = subprocess.run(
            test_command,
            shell=True,
            cwd=target_repo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error("runner: test command timed out after %ds", timeout)
        return RunResult(exit_code=1, output="TIMEOUT", failures=["TIMEOUT"])

    combined = proc.stdout + proc.stderr
    logger.info("runner: exit_code=%d output_len=%d", proc.returncode, len(combined))
    failures = _parse_failures(combined, test_command)
    return RunResult(exit_code=proc.returncode, output=combined, failures=failures)


def _parse_failures(output: str, test_command: str) -> list[str]:
    if "pytest" in test_command or "python" in test_command:
        return _parse_pytest(output)
    if "npm" in test_command or "jest" in test_command:
        return _parse_jest(output)
    return _parse_generic(output)


def _parse_pytest(output: str) -> list[str]:
    failures: list[str] = []
    # FAILED test_foo.py::test_bar - AssertionError
    for m in re.finditer(r"^FAILED (.+?)(?:\s+-\s+.+)?$", output, re.MULTILINE):
        failures.append(m.group(1).strip())
    # ERROR test_foo.py::test_bar
    for m in re.finditer(r"^ERROR (.+?)(?:\s+-\s+.+)?$", output, re.MULTILINE):
        name = m.group(1).strip()
        if name not in failures:
            failures.append(name)
    return failures


def _parse_jest(output: str) -> list[str]:
    failures: list[str] = []
    for m in re.finditer(r"●\s+(.+)", output):
        failures.append(m.group(1).strip())
    return failures


def _parse_generic(output: str) -> list[str]:
    failures: list[str] = []
    for line in output.splitlines():
        lower = line.lower()
        if any(kw in lower for kw in ("fail", "error", "assert")):
            failures.append(line.strip())
    return failures[:50]
