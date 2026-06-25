from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from healer.loop import run
from healer.state import HealerState


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

    state = HealerState(
        goal=args.goal,
        target_repo=target,
        test_command=args.test_cmd,
        max_cycles=args.max_cycles,
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
