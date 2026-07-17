from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import anthropic

from healer.agents.diagnoser import Diagnosis
from healer.agents.fixer import Patch

logger = logging.getLogger(__name__)

_CLIENT = anthropic.Anthropic()
_MODEL = "claude-opus-4-7"


@dataclass
class ReviewResult:
    approved: bool
    reason: str
    score: int  # 1-10


def review_patch(patch: Patch, diagnosis: Diagnosis, target_repo: str) -> ReviewResult:
    logger.info("reviewer: reviewing patch for %s", patch.file_path)

    if not patch.unified_diff and not patch.full_content:
        return ReviewResult(approved=False, reason="empty patch — nothing to apply", score=0)

    scope_check = _scope_guard(patch, diagnosis)
    if not scope_check:
        return ReviewResult(
            approved=False,
            reason=f"scope violation: patch touches {patch.file_path} which is not in affected files {diagnosis.affected_files}",
            score=0,
        )

    result = _llm_review(patch, diagnosis)
    logger.info("reviewer: approved=%s score=%d reason=%s", result.approved, result.score, result.reason)
    return result


def _scope_guard(patch: Patch, diagnosis: Diagnosis) -> bool:
    if not diagnosis.affected_files:
        return True
    if any(patch.file_path.endswith(f) or f.endswith(patch.file_path) for f in diagnosis.affected_files):
        return True
    # Check if diff header mentions an affected file
    for line in patch.unified_diff.splitlines():
        if line.startswith("+++ b/"):
            diff_file = line[6:].strip()
            if any(diff_file == f or f.endswith(diff_file) for f in diagnosis.affected_files):
                return True
    return False


def _llm_review(patch: Patch, diagnosis: Diagnosis) -> ReviewResult:
    diff_display = patch.unified_diff or f"[full file replacement: {patch.file_path}]\n{patch.full_content or ''}"
    prompt = f"""Review this patch for a failing test suite.

## Root Cause
{diagnosis.root_cause}

## Error Type
{diagnosis.error_type}

## Strategy Applied
{diagnosis.suggested_strategy}

## Patch
```diff
{diff_display}
```

## Rationale
{patch.rationale}

Evaluate:
1. Does this patch address the root cause?
2. Does it stay within scope (only touching files related to failures)?
3. Could it break other tests or introduce new bugs?

Respond in this exact format:
APPROVED: yes/no
SCORE: N (1-10)
REASON: one sentence"""

    response = _CLIENT.messages.create(
        model=_MODEL,
        max_tokens=512,
        system="You are a code reviewer. Evaluate patches for correctness and scope. Be strict.",
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text
    return _parse_review(raw)


def _parse_review(raw: str) -> ReviewResult:
    approved = False
    score = 5
    reason = "no reason given"

    m = re.search(r"APPROVED:\s*(yes|no)", raw, re.IGNORECASE)
    if m:
        approved = m.group(1).lower() == "yes"

    m = re.search(r"SCORE:\s*(\d+)", raw)
    if m:
        score = int(m.group(1))

    m = re.search(r"REASON:\s*(.+)", raw)
    if m:
        reason = m.group(1).strip()

    return ReviewResult(approved=approved, score=score, reason=reason)
