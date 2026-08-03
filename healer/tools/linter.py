from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

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
