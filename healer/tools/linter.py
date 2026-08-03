from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LintIssue:
    """A single static-analysis finding, normalized across linters."""

    file: str
    line: int
    code: str  # e.g. "F401", "attr-defined", "no-unused-vars"
    message: str
    severity: str = "error"  # error | warning | note
    tool: str = "unknown"

    @property
    def key(self) -> str:
        """Identity used for delta comparison. Line-insensitive: a patch that
        shifts line numbers must not look like it introduced new issues."""
        return f"{self.file}:{self.code}:{self.message}"

    def __str__(self) -> str:
        return f"{self.file}:{self.line} {self.code} {self.message}"


@dataclass
class LintResult:
    exit_code: int
    output: str  # combined stdout + stderr
    issues: list[LintIssue] = field(default_factory=list)
    tool: str = "unknown"
    command: str = ""

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    def issues_in(self, files: list[str]) -> list[LintIssue]:
        """Issues belonging to any of the given (relative) paths."""
        return [
            i
            for i in self.issues
            if any(i.file.endswith(f) or f.endswith(i.file) for f in files)
        ]

    def summary(self) -> str:
        if not self.issues:
            return f"{self.tool}: clean"
        return f"{self.tool}: {len(self.issues)} issue(s), {self.error_count} error(s)"


# ruff: "src/auth.py:12:1: F401 [*] `os` imported but unused"
_RUFF_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):(?P<col>\d+):\s+(?P<code>[A-Z]+\d+)\s+(?:\[\*\]\s+)?(?P<msg>.+)$",
    re.MULTILINE,
)


def parse_ruff(output: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for m in _RUFF_RE.finditer(output):
        issues.append(
            LintIssue(
                file=m.group("file").strip(),
                line=int(m.group("line")),
                code=m.group("code"),
                message=m.group("msg").strip(),
                severity="error",
                tool="ruff",
            )
        )
    return issues


# mypy: "healer/loop.py:41: error: Missing return statement  [return]"
_MYPY_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):(?:\d+:)?\s+(?P<sev>error|warning|note):\s+(?P<msg>.+?)(?:\s+\[(?P<code>[\w-]+)\])?$",
    re.MULTILINE,
)


def parse_mypy(output: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for m in _MYPY_RE.finditer(output):
        issues.append(
            LintIssue(
                file=m.group("file").strip(),
                line=int(m.group("line")),
                code=m.group("code") or "mypy",
                message=m.group("msg").strip(),
                severity=m.group("sev"),
                tool="mypy",
            )
        )
    return issues


# eslint stylish output groups issues under a bare file path:
#   /repo/src/app.js
#     12:3  error  'x' is assigned a value but never used  no-unused-vars
_ESLINT_FILE_RE = re.compile(r"^(?P<file>[^\s].*\.(?:js|jsx|ts|tsx|mjs|cjs))$")
_ESLINT_ISSUE_RE = re.compile(
    r"^\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<sev>error|warning)\s+(?P<msg>.+?)\s{2,}(?P<code>[\w@/-]+)\s*$"
)


def parse_eslint(output: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    current_file = ""
    for line in output.splitlines():
        fm = _ESLINT_FILE_RE.match(line)
        if fm:
            current_file = fm.group("file").strip()
            continue
        im = _ESLINT_ISSUE_RE.match(line)
        if im and current_file:
            issues.append(
                LintIssue(
                    file=current_file,
                    line=int(im.group("line")),
                    code=im.group("code"),
                    message=im.group("msg").strip(),
                    severity=im.group("sev"),
                    tool="eslint",
                )
            )
    return issues


def parse_generic(output: str) -> list[LintIssue]:
    """Last-resort parser for unknown linters: any file:line: message line."""
    issues: list[LintIssue] = []
    pattern = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):(?:\d+:)?\s*(?P<msg>.+)$", re.MULTILINE)
    for m in pattern.finditer(output):
        issues.append(
            LintIssue(
                file=m.group("file").strip(),
                line=int(m.group("line")),
                code="generic",
                message=m.group("msg").strip(),
                severity="error",
                tool="generic",
            )
        )
    return issues[:200]


def _parse_for(tool: str, output: str) -> list[LintIssue]:
    if tool == "ruff":
        return parse_ruff(output)
    if tool == "mypy":
        return parse_mypy(output)
    if tool == "eslint":
        return parse_eslint(output)
    return parse_generic(output)


def _tool_name(command: str) -> str:
    for name in ("ruff", "mypy", "eslint"):
        if name in command:
            return name
    return "generic"


def run_lint(lint_command: str, target_repo: str, timeout: int = 120) -> LintResult:
    """Run a lint command in target_repo. Never raises — a broken or missing
    linter yields an empty result so the heal loop keeps running on test signal
    alone. Every outcome is logged (no silent failures)."""
    tool = _tool_name(lint_command)
    logger.info("linter: running %r in %s (tool=%s)", lint_command, target_repo, tool)

    try:
        proc = subprocess.run(
            lint_command,
            shell=True,
            cwd=target_repo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error("linter: %s timed out after %ds — treating as no signal", tool, timeout)
        return LintResult(exit_code=1, output="TIMEOUT", issues=[], tool=tool, command=lint_command)
    except OSError as e:
        logger.error("linter: failed to launch %r — %s", lint_command, e)
        return LintResult(exit_code=1, output=str(e), issues=[], tool=tool, command=lint_command)

    combined = proc.stdout + proc.stderr
    issues = _parse_for(tool, combined)
    result = LintResult(
        exit_code=proc.returncode,
        output=combined,
        issues=issues,
        tool=tool,
        command=lint_command,
    )
    logger.info(
        "linter: exit_code=%d %s output_len=%d",
        proc.returncode,
        result.summary(),
        len(combined),
    )
    return result


def detect_lint_command(target_repo: str) -> str | None:
    """Pick a lint command for the target repo, or None if nothing suitable is
    installed. Python repos prefer ruff; JS/TS repos prefer eslint."""
    repo = Path(target_repo)

    is_python = any(
        (repo / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
    )
    is_js = (repo / "package.json").exists()

    if is_python and shutil.which("ruff"):
        return "ruff check ."
    if is_js and shutil.which("npx"):
        return "npx --no-install eslint ."
    if is_python and shutil.which("mypy"):
        return "mypy . --ignore-missing-imports"

    logger.info("linter: no lint command detected for %s — lint signal disabled", target_repo)
    return None


@dataclass
class LintDelta:
    introduced: list[LintIssue] = field(default_factory=list)
    resolved: list[LintIssue] = field(default_factory=list)

    @property
    def is_regression(self) -> bool:
        return any(i.severity == "error" for i in self.introduced)

    def summary(self) -> str:
        return f"+{len(self.introduced)} / -{len(self.resolved)} lint issue(s)"


def lint_delta(before: LintResult, after: LintResult) -> LintDelta:
    """Issues a patch introduced and resolved. Compared on LintIssue.key, so
    pure line shifts are not counted as regressions."""
    before_keys = {i.key for i in before.issues}
    after_keys = {i.key for i in after.issues}

    delta = LintDelta(
        introduced=[i for i in after.issues if i.key not in before_keys],
        resolved=[i for i in before.issues if i.key not in after_keys],
    )
    logger.info("linter: delta %s (regression=%s)", delta.summary(), delta.is_regression)
    for issue in delta.introduced:
        logger.warning("linter: introduced %s", issue)
    return delta


def format_issues(issues: list[LintIssue], limit: int = 20) -> str:
    """Compact rendering for agent prompts and escalation reports."""
    if not issues:
        return "(none)"
    lines = [f"- {i}" for i in issues[:limit]]
    if len(issues) > limit:
        lines.append(f"- ...and {len(issues) - limit} more")
    return "\n".join(lines)
