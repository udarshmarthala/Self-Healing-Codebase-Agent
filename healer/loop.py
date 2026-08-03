from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from healer.agents.diagnoser import diagnose
from healer.agents.fixer import generate_fix
from healer.agents.reviewer import review_patch
from healer.escalation import generate_report, write_report
from healer.state import HealerState
from healer.tools import git, linter, patcher
from healer.tools.runner import run_tests

logger = logging.getLogger(__name__)
_console = Console()


def run(state: HealerState) -> HealerState:
    logger.info("loop: start — target=%s cmd=%r max_cycles=%d", state.target_repo, state.test_command, state.max_cycles)
    _console.print(Panel(f"[bold green]Self-Healer[/bold green]  target=[cyan]{state.target_repo}[/cyan]  max_cycles=[yellow]{state.max_cycles}[/yellow]"))

    git.ensure_git_repo(state.target_repo)

    while True:
        state.cycle += 1
        _console.rule(f"[bold cyan]CYCLE {state.cycle} / {state.max_cycles}[/bold cyan]")
        logger.info("=== CYCLE %d / %d ===", state.cycle, state.max_cycles)

        # OBSERVE
        result = run_tests(state.test_command, state.target_repo)
        fingerprint = state.record_cycle_result(result.output, result.exit_code, result.failures)

        if result.exit_code == 0:
            _console.print("[bold green]✓ All tests pass — SUCCESS[/bold green]")
            logger.info("loop: all tests pass — SUCCESS")
            state.status = "success"
            return state

        _console.print(f"[red]✗ {len(result.failures)} failure(s) detected[/red]")
        logger.info("loop: %d failures detected", len(result.failures))

        lint_before = _lint(state)
        if lint_before is not None:
            state.record_lint_result(
                lint_before.tool,
                [str(i) for i in lint_before.issues],
                lint_before.error_count,
            )
            _console.print(f"[dim]lint: {lint_before.summary()}[/dim]")

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

        scoped_lint = (
            lint_before.issues_in([patch.file_path]) if lint_before is not None else []
        )
        review = review_patch(patch, diagnosis, state.target_repo, lint_issues=scoped_lint)
        if not review.approved:
            logger.warning("loop: reviewer rejected patch — %s", review.reason)
            # Don't apply; let next cycle try a different strategy
            state.record_patch(patch.file_path, patch.unified_diff, f"REJECTED: {review.reason}")
            continue

        _console.print(f"[green]✓ Reviewer approved (score={review.score}) — applying patch[/green]")
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

        # Lint regression gate: a patch that fixes a test but adds new static
        # errors is not progress. Fully-green runs are never blocked on lint.
        lint_after = _lint(state) if lint_before is not None else None
        if lint_before is not None and lint_after is not None:
            delta = linter.lint_delta(lint_before, lint_after)
            state.record_lint_result(
                lint_after.tool,
                [str(i) for i in lint_after.issues],
                lint_after.error_count,
                introduced=[str(i) for i in delta.introduced],
            )
            if delta.is_regression and verify.exit_code != 0:
                _console.print(
                    f"[yellow]↔ Patch introduced {len(delta.introduced)} lint error(s) — rolling back[/yellow]"
                )
                logger.warning(
                    "loop: lint regression (%s) — rolling back patch on %s",
                    delta.summary(),
                    patch.file_path,
                )
                git.rollback_to(state.target_repo, sha_before)
                state.record_patch(
                    patch.file_path,
                    patch.unified_diff,
                    f"LINT_REGRESSION: {linter.format_issues(delta.introduced, limit=5)}",
                )
                continue

        if curr_count < prev_count or verify.exit_code == 0:
            commit_msg = f"[healer] cycle-{state.cycle}: {patch.rationale[:72]}"
            git.commit(state.target_repo, commit_msg)
            state.record_patch(patch.file_path, patch.unified_diff, patch.rationale)
            _console.print(f"[green]↓ Progress: {prev_count} → {curr_count} failures[/green]")
            logger.info("loop: progress! failures %d → %d", prev_count, curr_count)
        else:
            _console.print("[yellow]↔ No improvement — rolling back[/yellow]")
            logger.warning("loop: no improvement — rolling back")
            git.rollback_to(state.target_repo, sha_before)
            state.record_patch(patch.file_path, patch.unified_diff, f"NO_PROGRESS: {patch.rationale}")

    return state  # unreachable, satisfies type checker


def _lint(state: HealerState) -> linter.LintResult | None:
    """Run the configured linter, or None when lint signal is disabled."""
    if not state.lint_command:
        return None
    return linter.run_lint(state.lint_command, state.target_repo)


def _escalate(state: HealerState, reason: str) -> HealerState:
    state.status = "escalated"
    report = generate_report(state, reason)
    report_path = Path(state.target_repo) / "HEALER_ESCALATION.md"
    write_report(state, reason, str(report_path))
    _console.print(Panel(f"[bold red]ESCALATED[/bold red]  reason=[yellow]{reason}[/yellow]\nReport: {report_path}"))
    logger.error("loop: ESCALATED — %s\nReport: %s\n%s", reason, report_path, report)
    return state
