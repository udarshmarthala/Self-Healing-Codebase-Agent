from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic

from healer.agents.diagnoser import Diagnosis

logger = logging.getLogger(__name__)

_CLIENT = anthropic.Anthropic()
_MODEL = "claude-opus-4-7"


@dataclass
class Patch:
    file_path: str
    unified_diff: str
    rationale: str
    full_content: str | None = None  # set when diff not applicable


def generate_fix(
    diagnosis: Diagnosis,
    target_repo: str,
    cycle: int,
) -> Patch:
    logger.info("fixer: generating fix for %s via strategy=%s", diagnosis.error_type, diagnosis.suggested_strategy)

    file_contents = _read_relevant_files(diagnosis.affected_files, target_repo)
    prompt = _build_prompt(diagnosis, file_contents, cycle)

    response = _CLIENT.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text
    logger.debug("fixer raw response: %s", raw[:500])
    return _parse_response(raw, diagnosis)


def _read_relevant_files(affected_files: list[str], target_repo: str) -> dict[str, str]:
    contents: dict[str, str] = {}
    for rel_path in affected_files[:5]:
        full = Path(target_repo) / rel_path
        if full.exists():
            try:
                contents[rel_path] = full.read_text()
            except Exception as e:
                logger.warning("fixer: could not read %s: %s", rel_path, e)
    return contents


def _build_prompt(diagnosis: Diagnosis, file_contents: dict[str, str], cycle: int) -> str:
    files_section = ""
    for path, content in file_contents.items():
        files_section += f"\n### {path}\n```python\n{content}\n```\n"

    errors_section = "\n".join(f"- {e}" for e in diagnosis.raw_errors[:10])
    tests_section = "\n".join(f"- {t}" for t in diagnosis.failing_tests[:10])

    return f"""Cycle {cycle} — fix needed.

## Root Cause
{diagnosis.root_cause}

## Error Type
{diagnosis.error_type}

## Strategy
{diagnosis.suggested_strategy}

## Failing Tests
{tests_section}

## Raw Errors
{errors_section}

## Relevant Files
{files_section}

Produce a minimal fix. Output ONLY:
1. A unified diff (```diff ... ```) OR full file replacement (```python path/to/file.py ... ```)
2. A one-line rationale after the code block: RATIONALE: <text>

Touch only files directly causing the failures. Do not refactor unrelated code."""


_SYSTEM_PROMPT = """You are a code repair agent. You receive a failing test diagnosis and produce the minimal patch to fix it.

Rules:
- Output a unified diff OR full file replacement — never both
- Touch only files directly related to the failing tests
- Do not add new features, refactor unrelated code, or change test files
- If the fix requires multiple files, output multiple diff blocks
- Prefer minimal changes — one line if possible"""


def _parse_response(raw: str, diagnosis: Diagnosis) -> Patch:
    import re

    rationale = "no rationale provided"
    m = re.search(r"RATIONALE:\s*(.+)", raw)
    if m:
        rationale = m.group(1).strip()

    # Try unified diff first
    diff_m = re.search(r"```diff\n(.*?)```", raw, re.DOTALL)
    if diff_m:
        unified_diff = diff_m.group(1)
        file_path = diagnosis.affected_files[0] if diagnosis.affected_files else "unknown"
        # Extract target file from diff header
        header_m = re.search(r"^\+\+\+ b/(.+)$", unified_diff, re.MULTILINE)
        if header_m:
            file_path = header_m.group(1).strip()
        return Patch(file_path=file_path, unified_diff=unified_diff, rationale=rationale)

    # Full file replacement
    file_m = re.search(r"```python (.+?)\n(.*?)```", raw, re.DOTALL)
    if file_m:
        file_path = file_m.group(1).strip()
        content = file_m.group(2)
        return Patch(
            file_path=file_path,
            unified_diff="",
            rationale=rationale,
            full_content=content,
        )

    # Fallback — return raw as rationale, empty patch
    logger.warning("fixer: could not parse structured patch from response")
    file_path = diagnosis.affected_files[0] if diagnosis.affected_files else "unknown"
    return Patch(file_path=file_path, unified_diff="", rationale=f"parse_failed: {raw[:200]}")
