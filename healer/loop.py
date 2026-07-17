from __future__ import annotations

import logging
from pathlib import Path

from healer.agents.diagnoser import diagnose
from healer.agents.fixer import generate_fix
from healer.agents.reviewer import review_patch
from healer.escalation import generate_report, write_report
from healer.state import HealerState
from healer.tools import git, patcher
from healer.tools.runner import run_tests

logger = logging.getLogger(__name__)


def run(state: HealerState) -> HealerState:
    logger.info("loop: start — target=%s cmd=%r max_cycles=%d", state.target_repo, state.test_command, state.max_cycles)

    git.ensure_git_repo(state.target_repo)

    while True:
        state.cycle += 1
        logger.info("=== CYCLE %d / %d ===", state.cycle, state.max_cycles)

        # OBSERVE
        result = run_tests(state.test_command, state.target_repo)
        fingerprint = state.record_cycle_result(result.output, result.exit_code, result.failures)

        if result.exit_code == 0:
            logger.info("loop: all tests pass — SUCCESS")
            state.status = "success"
            return state

        logger.info("loop: %d failures detected", len(result.failures))

        # Check exit conditions before reasoning
        if state.is_at_max_cycles():
            logger.warning("loop: max cycles reached — escalating")
            return _escalate(state, f"max_cycles ({state.max_cycles}) reached")

        if state.is_stalled(fingerprint):
            logger.warning("loop: stall detected (same failures 3+ cycles) — escalating")
            return _escalate(state, "stall (same failures for 3 consecutive cycles)")

        # REASON
        previous = [
            {"suggested_strategy": p.get("rationale", "")}
            for p in state.patches_applied
        ]
        diagnosis = diagnose(
            test_output=result.output,
            failures=result.failures,
            target_repo=state.target_repo,
            previous_diagnoses=previous,
        )

        # ACT
        sha_before = git.current_sha(state.target_repo)
        patch = generate_fix(diagnosis, state.target_repo, state.cycle)

        review = review_patch(patch, diagnosis, state.target_repo)
        if not review.approved:
            logger.warning("loop: reviewer rejected patch — %s", review.reason)
            # Don't apply; let next cycle try a different strategy
            state.record_patch(patch.file_path, patch.unified_diff, f"REJECTED: {review.reason}")
            continue

        logger.info("loop: reviewer approved (score=%d) — applying patch", review.score)

        try:
            if patch.full_content is not None:
                patcher.write_file(state.target_repo, patch.file_path, patch.full_content)
            else:
                patcher.apply_patch(state.target_repo, patch.unified_diff)
        except patcher.PatchError as e:
            logger.error("loop: patch apply failed — %s — rolling back", e)
            git.rollback_to(state.target_repo, sha_before)
            state.record_patch(patch.file_path, patch.unified_diff, f"APPLY_FAILED: {e}")
            continue

        # VERIFY — commit only on improvement; otherwise roll back
        verify = run_tests(state.test_command, state.target_repo)

        prev_count = len(result.failures)
        curr_count = len(verify.failures)

        if curr_count < prev_count or verify.exit_code == 0:
            commit_msg = f"[healer] cycle-{state.cycle}: {patch.rationale[:72]}"
            git.commit(state.target_repo, commit_msg)
            state.record_patch(patch.file_path, patch.unified_diff, patch.rationale)
            logger.info("loop: progress! failures %d → %d", prev_count, curr_count)
        else:
            logger.warning("loop: no improvement — rolling back")
            git.rollback_to(state.target_repo, sha_before)
            state.record_patch(patch.file_path, patch.unified_diff, f"NO_PROGRESS: {patch.rationale}")

    return state  # unreachable, satisfies type checker


def _escalate(state: HealerState, reason: str) -> HealerState:
    state.status = "escalated"
    report = generate_report(state, reason)
    report_path = Path(state.target_repo) / "HEALER_ESCALATION.md"
    write_report(state, reason, str(report_path))
    logger.error("loop: ESCALATED — %s\nReport: %s\n%s", reason, report_path, report)
    return state
