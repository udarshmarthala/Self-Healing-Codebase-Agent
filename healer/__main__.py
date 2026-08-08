from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from healer.loop import run
from healer.state import HealerState
from healer.tools import journal, sandbox
from healer.tools.coverage import supports_coverage
from healer.tools.linter import detect_lint_command


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="self-healer",
        description="Autonomous agent that fixes failing tests by iterative diagnosis and patching",
    )
    parser.add_argument("--target", required=True, help="Path to the repo to heal")
    parser.add_argument("--test-cmd", required=True, help='Test command, e.g. "pytest"')
    parser.add_argument("--goal", default="Make all tests pass", help="Human-readable goal")
    parser.add_argument("--max-cycles", type=int, default=10, help="Hard cap on loop iterations")
    parser.add_argument("--state-out", default=None, help="Write final state JSON to this path")
    parser.add_argument(
        "--lint-cmd",
        default=None,
        help='Lint command for the second signal, e.g. "ruff check .". Auto-detected if omitted',
    )
    parser.add_argument(
        "--no-lint",
        action="store_true",
        help="Disable the lint signal entirely (tests are then the only signal)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue the interrupted run recorded in <target>/.healer/journal.json "
        "instead of starting over (does not raise the max-cycles cap)",
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="Verify patches against a throwaway copy of the repo so test side effects "
        "cannot touch it (costs one copy per cycle)",
    )
    parser.add_argument(
        "--sandbox-max-mb",
        type=int,
        default=sandbox.DEFAULT_MAX_MB,
        help="Skip the sandbox for repos larger than this, verifying in place instead",
    )
    parser.add_argument(
        "--flake-retries",
        type=int,
        default=0,
        metavar="N",
        help="Re-run each failing test N times to tell flaky tests from real failures "
        "and skip trying to fix them (0 disables; 3 is a good starting point)",
    )
    parser.add_argument(
        "--flake-max-tests",
        type=int,
        default=10,
        metavar="N",
        help="Cap how many failing tests get re-run per cycle, since the cost is "
        "retries x tests extra suite runs",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Measure coverage each cycle and flag patched lines the suite never runs "
        "(pytest only; costs one extra suite run per cycle)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    target = str(Path(args.target).resolve())

    if args.no_lint:
        lint_command = None
    else:
        lint_command = args.lint_cmd or detect_lint_command(target)
    if lint_command:
        print(f"Lint signal: {lint_command}")

    coverage_enabled = args.coverage and supports_coverage(args.test_cmd)
    if args.coverage and not coverage_enabled:
        print(f"Coverage signal unavailable: {args.test_cmd!r} is not pytest-based")
    elif coverage_enabled:
        print("Coverage signal: on")

    if args.sandbox:
        print(f"Sandbox: on (max {args.sandbox_max_mb} MB)")

    if 0 < args.flake_retries < 2:
        print("Flake detection needs at least 2 retries to see a mixed result — disabling")
        args.flake_retries = 0
    elif args.flake_retries:
        print(f"Flake detection: {args.flake_retries} retries per failing test")

    resumed_events: list[journal.JournalEvent] = []

    if args.resume:
        state, resumed_events = _resume_state(
            target,
            args.test_cmd,
            lint_command,
            coverage_enabled,
            args.sandbox,
            args.sandbox_max_mb,
            args.flake_retries,
            args.flake_max_tests,
        )
    else:
        state = HealerState(
            goal=args.goal,
            target_repo=target,
            test_command=args.test_cmd,
            max_cycles=args.max_cycles,
            lint_command=lint_command,
            coverage_enabled=coverage_enabled,
            sandbox_enabled=args.sandbox,
            sandbox_max_mb=args.sandbox_max_mb,
            flake_retries=args.flake_retries,
            flake_max_tests=args.flake_max_tests,
        )

    final_state = run(state, resumed_events=resumed_events)

    if args.state_out:
        Path(args.state_out).write_text(final_state.to_json())

    if final_state.status == "success":
        print("\n✓ All tests pass.")
        sys.exit(0)
    else:
        print(f"\n✗ Healer exited with status: {final_state.status}")
        report_path = Path(target) / "HEALER_ESCALATION.md"
        if report_path.exists():
            print(f"  See escalation report: {report_path}")
        sys.exit(1)


def _resume_state(
    target: str,
    test_cmd: str,
    lint_command: str | None,
    coverage_enabled: bool,
    sandbox_enabled: bool,
    sandbox_max_mb: int,
    flake_retries: int,
    flake_max_tests: int,
) -> tuple[HealerState, list[journal.JournalEvent]]:
    """Rebuild state from the journal, or exit with a clear reason why not.

    Resuming is refused rather than silently downgraded to a fresh run: a fresh
    run would re-apply patches already committed and lose the stall history.
    """
    try:
        saved = journal.load(journal.journal_path(target))
        journal.assert_compatible(saved, target, test_cmd)
    except journal.JournalError as e:
        print(f"Cannot resume: {e}", file=sys.stderr)
        sys.exit(2)

    if journal.is_exhausted(saved):
        print(
            f"Cannot resume: that run already used all {saved.state.get('max_cycles')} cycles. "
            f"max_cycles caps the whole run across resumes — start a fresh run to go further.",
            file=sys.stderr,
        )
        sys.exit(2)

    state = HealerState.from_dict(saved.state)
    state.mark_resumed()
    # Signals are runtime switches, not part of the recorded run — honour the
    # flags given on the resuming invocation.
    state.lint_command = lint_command
    state.coverage_enabled = coverage_enabled
    state.sandbox_enabled = sandbox_enabled
    state.sandbox_max_mb = sandbox_max_mb
    state.flake_retries = flake_retries
    state.flake_max_tests = flake_max_tests

    print(f"Resuming from cycle {state.cycle} ({state.cycles_remaining()} cycle(s) left)")
    if saved.events:
        print(saved.timeline(limit=5))
    return state, saved.events


if __name__ == "__main__":
    main()
