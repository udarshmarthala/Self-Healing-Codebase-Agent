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
