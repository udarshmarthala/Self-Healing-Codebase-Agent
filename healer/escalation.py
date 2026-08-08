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
        *(
            [f"**Resumed From:** cycle {state.resumed_from_cycle} "
             f"(the cap covers the whole run, not each resume)"]
            if state.resumed_from_cycle is not None
            else []
        ),
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

    lines += _lint_section(state)
    lines += _coverage_section(state)
    lines += _flake_section(state)
    lines += _sandbox_section(state)
    lines += _resume_section(state)
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


def _lint_section(state: HealerState) -> list[str]:
    if not state.lint_by_cycle:
        return []

    latest = state.lint_by_cycle[-1]
    lines = [
        "",
        "## Static Analysis at Exit",
        f"**Linter:** {latest.get('tool', 'unknown')} (`{state.lint_command}`)",
        "**Open Issues:** "
        + f"{latest.get('issue_count', 0)} ({latest.get('error_count', 0)} error(s))",
        "",
    ]

    for issue in latest.get("issues", [])[:10]:
        lines.append(f"- {issue}")
    if latest.get("issue_count", 0) > 10:
        lines.append(f"- ...and {latest['issue_count'] - 10} more")

    regressions = state.lint_regressions()
    if regressions:
        lines += ["", "**Patches rolled back for introducing lint errors:**"]
        for entry in regressions:
            for issue in entry["introduced"][:3]:
                lines.append(f"- Cycle {entry['cycle']}: {issue}")

    return lines


def _coverage_section(state: HealerState) -> list[str]:
    if not state.coverage_by_cycle:
        return []

    latest = state.coverage_by_cycle[-1]
    lines = [
        "",
        "## Coverage at Exit",
        f"**Line Coverage:** {latest.get('rate', 0.0):.1%} "
        f"(change this cycle: {latest.get('rate_change', 0.0):+.1%})",
    ]

    declined = latest.get("files_declined") or []
    if declined:
        lines += ["", "**Files whose coverage declined:**"]
        lines += [f"- {path}" for path in declined]

    unverified = state.unverified_patches()
    if unverified:
        lines += [
            "",
            "**Patched lines the test suite never executed** — these fixes are "
            "unverified even where tests went green:",
        ]
        for entry in unverified:
            numbers = ", ".join(str(n) for n in entry["untested_patch_lines"][:10])
            lines.append(f"- Cycle {entry['cycle']}: line(s) {numbers}")

    return lines


def _flake_section(state: HealerState) -> list[str]:
    """Quarantined tests are the one failure class the healer cannot fix."""
    if not state.known_flaky:
        return []

    lines = [
        "",
        "## Flaky Tests (quarantined)",
        "These tests both passed and failed with the code unchanged, so no patch "
        "can make them reliably pass. The healer stopped trying to fix them:",
    ]
    lines += [f"- {test_id}" for test_id in sorted(state.known_flaky)]
    lines += [
        "",
        "Fix the nondeterminism itself — shared state between tests, real clocks, "
        "network calls, or ordering assumptions.",
    ]
    return lines


def _sandbox_section(state: HealerState) -> list[str]:
    """Only worth reporting when isolation was requested but not achieved."""
    declined = state.unsandboxed_cycles()
    if not declined:
        return []

    lines = [
        "",
        "## Sandbox",
        "These cycles were verified against the real repo because isolation was "
        "declined — any test side effects landed in your working tree:",
    ]
    for entry in declined:
        lines.append(f"- Cycle {entry['cycle']}: {entry['reason'] or 'unknown reason'}")
    return lines


def _resume_section(state: HealerState) -> list[str]:
    """Note that this run was continued, so the cycle count reads correctly."""
    if state.resumed_from_cycle is None:
        return []
    return [
        "",
        "## Run Continuity",
        f"Journal: `{state.target_repo}/.healer/journal.json` — "
        f"re-run with `--resume` to continue from here, budget permitting.",
    ]


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
    if state.known_flaky and not state.patches_applied:
        return (
            "No patches were applied because every failure was flaky. Stabilise the "
            "quarantined tests listed above, then re-run the healer."
        )
    if not state.patches_applied:
        return "No patches were applied. Check if the test command and target repo path are correct."

    last_patch = state.patches_applied[-1]
    last_file = last_patch.get("file", "unknown")
    return (
        f"Manually inspect `{last_file}` and the latest failing tests. "
        f"Review `patches_applied` in the state dump for what was attempted. "
        f"Consider whether environment variables, external services, or test fixtures need updating."
    )
