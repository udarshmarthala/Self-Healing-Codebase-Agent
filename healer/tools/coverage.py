from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

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
