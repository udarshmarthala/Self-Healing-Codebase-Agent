from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from healer.loop import run
from healer.state import HealerState
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

    state = HealerState(
        goal=args.goal,
        target_repo=target,
        test_command=args.test_cmd,
        max_cycles=args.max_cycles,
        lint_command=lint_command,
        coverage_enabled=coverage_enabled,
    )

    final_state = run(state)

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


if __name__ == "__main__":
    main()
