from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


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
    logger.info("diagnoser: analyzing %d failures", len(failures))

    error_type = _classify_error(test_output, failures)
    affected_files = _extract_affected_files(test_output, target_repo)
    raw_errors = _extract_raw_errors(test_output)
    root_cause = _infer_root_cause(test_output, error_type, affected_files)
    strategy = _pick_strategy(error_type, previous_diagnoses or [])

    diagnosis = Diagnosis(
        root_cause=root_cause,
        affected_files=affected_files,
        failing_tests=failures,
        error_type=error_type,
        suggested_strategy=strategy,
        raw_errors=raw_errors,
    )
    logger.info("diagnoser: error_type=%s files=%s", error_type, affected_files)
    return diagnosis


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
    # Python tracebacks: "  File "path/to/file.py", line N"
    for m in re.finditer(r'File "([^"]+\.py)"', output):
        path = m.group(1)
        # Normalize to relative path if inside repo
        try:
            rel = str(Path(path).relative_to(target_repo))
            if rel not in files:
                files.append(rel)
        except ValueError:
            if path not in files:
                files.append(path)
    # pytest short form: "test_foo.py::test_bar"
    for m in re.finditer(r"([\w/]+\.py)::", output):
        path = m.group(1)
        if path not in files:
            files.append(path)
    return files[:10]


def _extract_raw_errors(output: str) -> list[str]:
    errors: list[str] = []
    for m in re.finditer(r"^(E\s+.+)$", output, re.MULTILINE):
        errors.append(m.group(1).strip())
    return errors[:20]


def _infer_root_cause(output: str, error_type: str, affected_files: list[str]) -> str:
    # Extract the most specific error line
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
