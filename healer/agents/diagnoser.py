from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

_CLIENT = anthropic.Anthropic()
_MODEL = "claude-opus-4-7"

_TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": "Read a source file from the target repository to understand the code causing failures.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to repo root (e.g. 'src/auth.py')",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "report_diagnosis",
        "description": "Report the final diagnosis after reading and analyzing relevant source files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "root_cause": {
                    "type": "string",
                    "description": "One-sentence description of the root cause",
                },
                "affected_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Relative paths of files that need to be changed",
                },
                "error_type": {
                    "type": "string",
                    "description": "Error category: import_error, assertion_error, attribute_error, type_error, name_error, syntax_error, value_error, key_error, index_error, unknown_error",
                },
                "suggested_strategy": {
                    "type": "string",
                    "description": "Fix strategy: fix_import, fix_logic, fix_syntax, add_missing_attribute, fix_type_mismatch, define_variable, etc.",
                },
            },
            "required": ["root_cause", "affected_files", "error_type", "suggested_strategy"],
        },
    },
]

_SYSTEM = [
    {
        "type": "text",
        "text": (
            "You are a code diagnosis agent. Analyze failing test output, read relevant source files "
            "using the read_file tool, then report a precise diagnosis using report_diagnosis.\n\n"
            "Rules:\n"
            "- Read source files mentioned in tracebacks before diagnosing\n"
            "- Do not read test files unless the failure is in the test setup itself\n"
            "- Pick a strategy not already tried in previous cycles\n"
            "- Prefer the most specific error_type that matches\n"
            "- Be concise: one root cause sentence, minimal affected files"
        ),
        "cache_control": {"type": "ephemeral"},
    }
]


@dataclass
class Diagnosis:
    root_cause: str
    affected_files: list[str]
    failing_tests: list[str]
    error_type: str
    suggested_strategy: str
    raw_errors: list[str]


def diagnose(
    test_output: str,
    failures: list[str],
    target_repo: str,
    previous_diagnoses: list[dict] | None = None,
) -> Diagnosis:
    logger.info("diagnoser: analyzing %d failures via Claude tool-use", len(failures))
    raw_errors = _extract_raw_errors(test_output)
    result = _agentic_diagnose(
        test_output, failures, raw_errors, target_repo, previous_diagnoses or []
    )
    logger.info("diagnoser: error_type=%s files=%s", result.error_type, result.affected_files)
    return result


def _agentic_diagnose(
    test_output: str,
    failures: list[str],
    raw_errors: list[str],
    target_repo: str,
    previous_diagnoses: list[dict],
) -> Diagnosis:
    prev_section = ""
    if previous_diagnoses:
        prev_section = "\n## Previously Tried Strategies (avoid repeating)\n" + "\n".join(
            f"- {d.get('suggested_strategy', '')}" for d in previous_diagnoses if d.get("suggested_strategy")
        )

    user_msg = (
        f"## Test Output\n```\n{test_output[:3000]}\n```\n\n"
        f"## Failing Tests\n" + "\n".join(f"- {f}" for f in failures[:10]) + "\n\n"
        f"## Raw Errors\n" + "\n".join(f"- {e}" for e in raw_errors[:10])
        + prev_section
        + "\n\nRead the relevant source files, then call report_diagnosis."
    )

    messages: list[dict] = [{"role": "user", "content": user_msg}]

    for _ in range(10):
        response = _CLIENT.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=_SYSTEM,
            tools=_TOOLS,
            messages=messages,
        )

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = []
        diagnosis_input = None

        for tu in tool_uses:
            if tu.name == "read_file":
                path = tu.input.get("path", "")
                content = _safe_read(target_repo, path)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": content}
                )
            elif tu.name == "report_diagnosis":
                diagnosis_input = tu.input
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": "Diagnosis recorded."}
                )

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        if diagnosis_input is not None:
            return Diagnosis(
                root_cause=diagnosis_input.get("root_cause", "unknown"),
                affected_files=diagnosis_input.get("affected_files", []),
                failing_tests=failures,
                error_type=diagnosis_input.get("error_type", "unknown_error"),
                suggested_strategy=diagnosis_input.get("suggested_strategy", "read_traceback"),
                raw_errors=raw_errors,
            )

        if response.stop_reason == "end_turn":
            break

    logger.warning("diagnoser: agent loop ended without report_diagnosis — falling back to heuristics")
    return _fallback_diagnose(test_output, failures, raw_errors, target_repo, previous_diagnoses)


def _safe_read(target_repo: str, path: str) -> str:
    full = Path(target_repo) / path
    try:
        if full.exists() and full.is_file():
            content = full.read_text()
            logger.debug("diagnoser: read %s (%d chars)", path, len(content))
            return content[:8000]
        return f"File not found: {path}"
    except Exception as exc:
        return f"Error reading {path}: {exc}"


def _extract_raw_errors(output: str) -> list[str]:
    errors: list[str] = []
    for m in re.finditer(r"^(E\s+.+)$", output, re.MULTILINE):
        errors.append(m.group(1).strip())
    return errors[:20]


# ── Heuristic fallback (used when agent loop fails) ──────────────────────────

def _fallback_diagnose(
    test_output: str,
    failures: list[str],
    raw_errors: list[str],
    target_repo: str,
    previous_diagnoses: list[dict],
) -> Diagnosis:
    error_type = _classify_error(test_output, failures)
    affected_files = _extract_affected_files(test_output, target_repo)
    root_cause = _infer_root_cause(test_output, error_type, affected_files)
    strategy = _pick_strategy(error_type, previous_diagnoses)
    return Diagnosis(
        root_cause=root_cause,
        affected_files=affected_files,
        failing_tests=failures,
        error_type=error_type,
        suggested_strategy=strategy,
        raw_errors=raw_errors,
    )


def _classify_error(output: str, failures: list[str]) -> str:
    lower = output.lower()
    if "importerror" in lower or "modulenotfounderror" in lower:
        return "import_error"
    if "assertionerror" in lower:
        return "assertion_error"
    if "attributeerror" in lower:
        return "attribute_error"
    if "typeerror" in lower:
        return "type_error"
    if "nameerror" in lower:
        return "name_error"
    if "syntaxerror" in lower:
        return "syntax_error"
    if "indentationerror" in lower:
        return "indentation_error"
    if "valueerror" in lower:
        return "value_error"
    if "keyerror" in lower:
        return "key_error"
    if "indexerror" in lower:
        return "index_error"
    if "timeout" in lower:
        return "timeout"
    return "unknown_error"


def _extract_affected_files(output: str, target_repo: str) -> list[str]:
    files: list[str] = []
    for m in re.finditer(r'File "([^"]+\.py)"', output):
        path = m.group(1)
        try:
            rel = str(Path(path).relative_to(target_repo))
            if rel not in files:
                files.append(rel)
        except ValueError:
            if path not in files:
                files.append(path)
    for m in re.finditer(r"([\w/]+\.py)::", output):
        path = m.group(1)
        if path not in files:
            files.append(path)
    return files[:10]


def _infer_root_cause(output: str, error_type: str, affected_files: list[str]) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("E ") and len(stripped) > 3:
            return stripped[2:].strip()
    if affected_files:
        return f"{error_type} in {affected_files[0]}"
    return f"Unknown {error_type} — see raw output"


def _pick_strategy(error_type: str, previous_diagnoses: list[dict]) -> str:
    seen_strategies = {d.get("suggested_strategy") for d in previous_diagnoses}
    strategies = {
        "import_error": ["fix_import", "add_dependency", "check_module_path"],
        "assertion_error": ["fix_logic", "update_expected_value", "check_data_flow"],
        "attribute_error": ["fix_attribute_name", "add_missing_attribute", "check_class_init"],
        "type_error": ["fix_type_mismatch", "add_type_conversion", "check_function_signature"],
        "name_error": ["define_variable", "fix_scope", "add_import"],
        "syntax_error": ["fix_syntax", "check_indentation", "validate_brackets"],
        "indentation_error": ["fix_indentation"],
        "value_error": ["validate_input", "fix_value_conversion"],
        "key_error": ["add_default", "check_dict_keys", "use_get_method"],
        "index_error": ["check_list_bounds", "add_length_guard"],
        "unknown_error": ["read_traceback", "add_debug_logging", "isolate_failure"],
    }
    candidates = strategies.get(error_type, strategies["unknown_error"])
    for s in candidates:
        if s not in seen_strategies:
            return s
    return candidates[-1]
