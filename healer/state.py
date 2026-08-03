from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class HealerState:
    goal: str
    target_repo: str
    test_command: str
    cycle: int = 0
    max_cycles: int = 10
    status: Literal["in_progress", "success", "escalated"] = "in_progress"
    failures_by_cycle: list[dict] = field(default_factory=list)
    patches_applied: list[dict] = field(default_factory=list)
    stall_counter: int = 0
    error_fingerprints: set[str] = field(default_factory=set)
    lint_command: str | None = None
    lint_by_cycle: list[dict] = field(default_factory=list)
    coverage_enabled: bool = False
    coverage_by_cycle: list[dict] = field(default_factory=list)

    def record_cycle_result(self, test_output: str, exit_code: int, failures: list[str]) -> str:
        fingerprint = _fingerprint(failures)
        entry = {
            "cycle": self.cycle,
            "exit_code": exit_code,
            "failures": failures,
            "fingerprint": fingerprint,
            "output_snippet": test_output[:2000],
        }
        self.failures_by_cycle.append(entry)
        return fingerprint

    def record_patch(self, file_path: str, diff: str, rationale: str) -> None:
        self.patches_applied.append({
            "cycle": self.cycle,
            "file": file_path,
            "diff": diff,
            "rationale": rationale,
        })

    def record_lint_result(
        self,
        tool: str,
        issues: list[str],
        error_count: int,
        introduced: list[str] | None = None,
    ) -> None:
        """Store a cycle's lint snapshot. Issues are stored as rendered strings
        (path:line code message), never file contents."""
        self.lint_by_cycle.append({
            "cycle": self.cycle,
            "tool": tool,
            "issue_count": len(issues),
            "error_count": error_count,
            "issues": issues[:50],
            "introduced": (introduced or [])[:50],
        })

    def record_coverage_result(
        self,
        rate: float,
        rate_change: float = 0.0,
        untested_patch_lines: list[int] | None = None,
        files_declined: list[str] | None = None,
    ) -> None:
        """Store a cycle's coverage snapshot: rates and line numbers only."""
        self.coverage_by_cycle.append({
            "cycle": self.cycle,
            "rate": round(rate, 4),
            "rate_change": round(rate_change, 4),
            "untested_patch_lines": (untested_patch_lines or [])[:50],
            "files_declined": (files_declined or [])[:20],
        })

    def unverified_patches(self) -> list[dict]:
        """Cycles whose patch added lines the test suite never executed."""
        return [c for c in self.coverage_by_cycle if c["untested_patch_lines"]]

    def lint_regressions(self) -> list[dict]:
        """Cycles where the applied patch introduced new lint issues."""
        return [c for c in self.lint_by_cycle if c["introduced"]]

    def is_stalled(self, fingerprint: str) -> bool:
        if fingerprint in self.error_fingerprints:
            self.stall_counter += 1
        else:
            self.stall_counter = 0
            self.error_fingerprints.add(fingerprint)
        return self.stall_counter >= 3

    def is_at_max_cycles(self) -> bool:
        return self.cycle >= self.max_cycles

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["error_fingerprints"] = list(self.error_fingerprints)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "HealerState":
        d = dict(d)
        d["error_fingerprints"] = set(d.get("error_fingerprints", []))
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _fingerprint(failures: list[str]) -> str:
    combined = "\n".join(sorted(failures))
    return hashlib.sha256(combined.encode()).hexdigest()[:16]
