from __future__ import annotations

import json
import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FileCoverage:
    """Line coverage for a single source file, normalized across report formats."""

    path: str
    statements: int
    missing_lines: list[int] = field(default_factory=list)

    @property
    def covered(self) -> int:
        return max(self.statements - len(self.missing_lines), 0)

    @property
    def rate(self) -> float:
        """Fraction of statements covered, 0.0-1.0. Empty files count as covered."""
        if self.statements == 0:
            return 1.0
        return self.covered / self.statements

    def uncovered_among(self, lines: list[int]) -> list[int]:
        missing = set(self.missing_lines)
        return sorted(line for line in lines if line in missing)


@dataclass
class CoverageResult:
    files: dict[str, FileCoverage] = field(default_factory=dict)
    available: bool = True  # False when coverage could not be measured
    source: str = "unknown"  # xml | json | term | none

    @property
    def total_statements(self) -> int:
        return sum(f.statements for f in self.files.values())

    @property
    def total_missing(self) -> int:
        return sum(len(f.missing_lines) for f in self.files.values())

    @property
    def rate(self) -> float:
        total = self.total_statements
        if total == 0:
            return 1.0
        return (total - self.total_missing) / total

    def get(self, path: str) -> FileCoverage | None:
        """Look up a file by exact or suffix match — report paths and patch paths
        are both repo-relative but may differ by leading directory."""
        if path in self.files:
            return self.files[path]
        for key, cov in self.files.items():
            if key.endswith(path) or path.endswith(key):
                return cov
        return None

    def summary(self) -> str:
        if not self.available:
            return "coverage: unavailable"
        return (
            f"coverage: {self.rate:.1%} "
            f"({self.total_statements - self.total_missing}/{self.total_statements} statements)"
        )


def parse_cobertura_xml(xml_text: str) -> CoverageResult:
    """Parse coverage.py's Cobertura XML (`--cov-report=xml`), the most precise
    of the three formats: it carries per-line hit counts."""
    result = CoverageResult(source="xml")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("coverage: malformed cobertura XML — %s", e)
        return CoverageResult(available=False, source="xml")

    for cls in root.iter("class"):
        path = cls.get("filename")
        if not path:
            continue
        statements = 0
        missing: list[int] = []
        for line in cls.iter("line"):
            number = line.get("number")
            hits = line.get("hits")
            if number is None or hits is None:
                continue
            statements += 1
            if int(hits) == 0:
                missing.append(int(number))

        existing = result.files.get(path)
        if existing:  # same file split across <package> entries
            existing.statements += statements
            existing.missing_lines = sorted(set(existing.missing_lines) | set(missing))
        else:
            result.files[path] = FileCoverage(
                path=path, statements=statements, missing_lines=sorted(missing)
            )

    return result


def parse_coverage_json(json_text: str) -> CoverageResult:
    """Parse coverage.py's JSON report (`--cov-report=json`).

    Shape: {"files": {"src/a.py": {"summary": {"num_statements": N},
                                   "missing_lines": [1, 2]}}}
    """
    result = CoverageResult(source="json")
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.error("coverage: malformed JSON report — %s", e)
        return CoverageResult(available=False, source="json")

    for path, entry in (data.get("files") or {}).items():
        summary = entry.get("summary") or {}
        missing = [int(n) for n in entry.get("missing_lines", [])]
        statements = int(summary.get("num_statements", len(entry.get("executed_lines", [])) + len(missing)))
        result.files[path] = FileCoverage(
            path=path, statements=statements, missing_lines=sorted(missing)
        )

    return result


# term-missing row: "src/auth.py   45   6   87%   12-14, 20"
_TERM_ROW_RE = re.compile(
    r"^(?P<path>[\w./\\-]+\.\w+)\s+(?P<stmts>\d+)\s+(?P<miss>\d+)\s+(?:\d+%)(?:\s+(?P<lines>[\d,\s-]+))?$"
)


def _expand_ranges(spec: str) -> list[int]:
    """"12-14, 20" -> [12, 13, 14, 20]. Branch markers like "18->20" are skipped."""
    lines: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk or "->" in chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            if start.strip().isdigit() and end.strip().isdigit():
                lines.extend(range(int(start), int(end) + 1))
        elif chunk.isdigit():
            lines.append(int(chunk))
    return lines


def parse_term_missing(output: str) -> CoverageResult:
    """Parse the `--cov-report=term-missing` table. Least precise of the three
    formats — used only when no machine-readable report was produced."""
    result = CoverageResult(source="term")
    for line in output.splitlines():
        if line.strip().startswith("TOTAL"):
            continue
        m = _TERM_ROW_RE.match(line.strip())
        if not m:
            continue
        missing = _expand_ranges(m.group("lines") or "")
        result.files[m.group("path")] = FileCoverage(
            path=m.group("path"),
            statements=int(m.group("stmts")),
            missing_lines=sorted(missing),
        )
    return result


_REPORT_FILENAME = ".healer-coverage.json"


def supports_coverage(test_command: str) -> bool:
    """Only pytest-based suites are instrumented. Anything else (npm, make,
    custom scripts) runs unchanged and yields no coverage signal."""
    return "pytest" in test_command


def build_coverage_command(test_command: str, report_path: str = _REPORT_FILENAME) -> str:
    """Wrap a pytest command with coverage reporting. Existing --cov flags are
    respected; only the JSON report is appended so the user's own reports and
    thresholds are untouched."""
    if not supports_coverage(test_command):
        return test_command

    parts = [test_command]
    if "--cov" not in test_command:
        parts.append("--cov=.")
    parts.append(f"--cov-report=json:{report_path}")
    return " ".join(parts)


def read_report(target_repo: str, report_path: str = _REPORT_FILENAME) -> CoverageResult:
    """Load whichever coverage report exists in the repo, newest format first."""
    repo = Path(target_repo)

    json_report = repo / report_path
    if json_report.exists():
        return parse_coverage_json(json_report.read_text())

    xml_report = repo / "coverage.xml"
    if xml_report.exists():
        return parse_cobertura_xml(xml_report.read_text())

    logger.info("coverage: no report found in %s", target_repo)
    return CoverageResult(available=False, source="none")


def measure(test_command: str, target_repo: str, timeout: int = 300) -> CoverageResult:
    """Run the test suite with coverage instrumentation and return the result.

    Never raises. Coverage is advisory — pass/fail always comes from runner.py,
    so a missing pytest-cov or a broken report degrades to `available=False`.
    """
    if not supports_coverage(test_command):
        logger.info("coverage: %r is not pytest-based — no coverage signal", test_command)
        return CoverageResult(available=False, source="none")

    command = build_coverage_command(test_command)
    logger.info("coverage: running %r in %s", command, target_repo)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=target_repo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error("coverage: timed out after %ds — no coverage signal", timeout)
        return CoverageResult(available=False, source="none")
    except OSError as e:
        logger.error("coverage: failed to launch %r — %s", command, e)
        return CoverageResult(available=False, source="none")

    combined = proc.stdout + proc.stderr
    result = read_report(target_repo)

    if not result.available:
        result = parse_term_missing(combined)
        result.available = bool(result.files)
        if not result.available:
            logger.warning("coverage: no parseable report (exit_code=%d)", proc.returncode)

    logger.info("coverage: exit_code=%d %s", proc.returncode, result.summary())
    return result


# Coverage moves for reasons unrelated to the patch (a newly passing test
# executes more lines), so only a drop beyond this margin counts as a decline.
DROP_TOLERANCE = 0.02


@dataclass
class CoverageDelta:
    before_rate: float
    after_rate: float
    files_declined: list[str] = field(default_factory=list)

    @property
    def rate_change(self) -> float:
        return self.after_rate - self.before_rate

    @property
    def declined(self) -> bool:
        return self.rate_change < -DROP_TOLERANCE

    def summary(self) -> str:
        arrow = "↑" if self.rate_change >= 0 else "↓"
        return f"coverage {self.before_rate:.1%} → {self.after_rate:.1%} ({arrow}{abs(self.rate_change):.1%})"


def coverage_delta(before: CoverageResult, after: CoverageResult) -> CoverageDelta:
    """Compare two coverage runs. Unavailable measurements yield a no-op delta
    so callers never act on a phantom drop."""
    if not before.available or not after.available:
        return CoverageDelta(before_rate=0.0, after_rate=0.0)

    declined = []
    for path, after_file in after.files.items():
        before_file = before.get(path)
        if before_file and after_file.rate < before_file.rate - DROP_TOLERANCE:
            declined.append(path)

    delta = CoverageDelta(
        before_rate=before.rate,
        after_rate=after.rate,
        files_declined=sorted(declined),
    )
    logger.info("coverage: delta %s (declined=%s)", delta.summary(), delta.declined)
    return delta


# Unified diff hunk header: "@@ -12,3 +14,7 @@"
_HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(?P<start>\d+)(?:,(?P<count>\d+))?\s+@@")


def added_lines(unified_diff: str) -> list[int]:
    """Post-patch line numbers of lines a unified diff adds."""
    lines: list[int] = []
    current = 0
    for line in unified_diff.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            current = int(m.group("start"))
            continue
        if current == 0 or line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("+"):
            lines.append(current)
            current += 1
        elif line.startswith("-"):
            continue  # removed lines do not advance the post-patch counter
        else:
            current += 1
    return lines


def untested_patch_lines(
    result: CoverageResult, file_path: str, unified_diff: str
) -> list[int]:
    """Lines the patch added that the test suite never executed.

    This is the sharpest signal the healer has that a fix is unverified: the
    tests went green without ever running the new code.
    """
    if not result.available or not unified_diff:
        return []

    file_cov = result.get(file_path)
    if file_cov is None:
        return []

    untested = file_cov.uncovered_among(added_lines(unified_diff))
    if untested:
        logger.warning(
            "coverage: %s has %d added line(s) never executed by the suite: %s",
            file_path,
            len(untested),
            untested[:10],
        )
    return untested
