from __future__ import annotations

from datetime import datetime

from healer.state import HealerState


def generate_report(state: HealerState, exit_reason: str) -> str:
    failing = _latest_failures(state)
    tried = _summarize_attempts(state)
    hypothesis = _root_cause_hypothesis(state)
    recommended_action = _recommended_action(state)

    lines = [
        "# Healer Escalation Report",
        "",
        f"**Target:** {state.target_repo}",
        f"**Cycles Run:** {state.cycle} / {state.max_cycles}",
        f"**Exit Reason:** {exit_reason}",
        f"**Timestamp:** {datetime.utcnow().isoformat()}Z",
        "",
        "## Failing Tests at Exit",
    ]

    if failing:
        for f in failing:
            lines.append(f"- {f}")
    else:
        lines.append("- (none captured)")

    lines += ["", "## What Was Tried"]
    if tried:
        for i, entry in enumerate(tried, 1):
            lines.append(f"{i}. {entry}")
    else:
        lines.append("- No patches attempted")

    lines += ["", "## Root Cause Hypothesis", hypothesis]
    lines += ["", "## Recommended Human Action", recommended_action]

    return "\n".join(lines)


def write_report(state: HealerState, exit_reason: str, output_path: str) -> None:
    report = generate_report(state, exit_reason)
    with open(output_path, "w") as f:
        f.write(report)


def _latest_failures(state: HealerState) -> list[str]:
    if not state.failures_by_cycle:
        return []
    return state.failures_by_cycle[-1].get("failures", [])


def _summarize_attempts(state: HealerState) -> list[str]:
    summary = []
    for patch in state.patches_applied:
        cycle = patch.get("cycle", "?")
        file_ = patch.get("file", "unknown")
        rationale = patch.get("rationale", "no rationale")

        # Find if this cycle made progress
        cycle_results = [c for c in state.failures_by_cycle if c.get("cycle") == cycle]
        prev_results = [c for c in state.failures_by_cycle if c.get("cycle") == cycle - 1]

        prev_count = len(prev_results[0].get("failures", [])) if prev_results else "?"
        curr_count = len(cycle_results[0].get("failures", [])) if cycle_results else "?"

        if isinstance(prev_count, int) and isinstance(curr_count, int):
            delta = prev_count - curr_count
            progress = f"→ {'+' if delta > 0 else ''}{delta} failures" if delta != 0 else "→ no improvement"
        else:
            progress = ""

        summary.append(f"Cycle {cycle}: [{file_}] {rationale} {progress}")

    return summary


def _root_cause_hypothesis(state: HealerState) -> str:
    if not state.failures_by_cycle:
        return "No test runs captured."
    latest = state.failures_by_cycle[-1]
    failures = latest.get("failures", [])
    snippet = latest.get("output_snippet", "")

    if not failures:
        return "Tests passed before escalation — unexpected state."

    first_failure = failures[0]
    return (
        f"Consistent failure at `{first_failure}`. "
        f"Diagnoser was unable to produce a fix that resolved this after {state.cycle} cycles. "
        f"This may require environment configuration, external dependencies, or architectural changes "
        f"beyond what automated patching can address."
    )


def _recommended_action(state: HealerState) -> str:
    if not state.patches_applied:
        return "No patches were applied. Check if the test command and target repo path are correct."

    last_patch = state.patches_applied[-1]
    last_file = last_patch.get("file", "unknown")
    return (
        f"Manually inspect `{last_file}` and the latest failing tests. "
        f"Review `patches_applied` in the state dump for what was attempted. "
        f"Consider whether environment variables, external services, or test fixtures need updating."
    )
