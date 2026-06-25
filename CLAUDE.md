# Self-Healing Codebase Agent

An autonomous loop-engineering CLI tool that detects failing tests, diagnoses root causes,
writes fixes, re-runs verification, and iterates until green — or escalates with a structured report.

---

## Project Layout

```
self-healer/
├── healer/
│   ├── loop.py          # Main agent loop (observe → reason → act → verify)
│   ├── agents/
│   │   ├── diagnoser.py # Reads errors, identifies root cause
│   │   ├── fixer.py     # Writes code changes
│   │   └── reviewer.py  # Validates fix before applying
│   ├── tools/
│   │   ├── runner.py    # Executes test suite, captures stdout/stderr
│   │   ├── patcher.py   # Applies file diffs safely
│   │   └── git.py       # Commit, diff, rollback helpers
│   ├── state.py         # Blackboard: goal, cycle count, history, status
│   └── escalation.py    # Generates human-readable failure reports
├── tests/               # Tests for the healer itself
├── docs/
│   ├── architecture.md
│   ├── loop-design.md
│   └── escalation-schema.md
├── examples/            # Sample broken repos for demo/testing
├── pyproject.toml
└── CLAUDE.md            # This file
```

---

## Build & Run Commands

```bash
# Install
pip install -e ".[dev]"

# Run healer on a target repo
python -m healer.loop --target ./path/to/repo --test-cmd "pytest" --max-cycles 10

# Run healer's own tests
pytest tests/ -v

# Lint
ruff check healer/ tests/
mypy healer/

# Type check only
mypy healer/ --ignore-missing-imports
```

---

## Core Loop Architecture

The agent runs a strict observe → reason → act → verify cycle. Never skip a phase.

```
START
  │
  ▼
[OBSERVE]   Run test suite. Capture full stdout/stderr. Parse failures.
  │
  ▼
[REASON]    Diagnoser agent reads errors + relevant source files. Identifies root cause.
  │
  ▼
[ACT]       Fixer agent writes a patch. Reviewer agent approves before applying.
  │
  ▼
[VERIFY]    Re-run test suite. Compare results to previous cycle.
  │
  ├── All tests pass → EXIT (success)
  ├── Progress made → Loop back to OBSERVE
  ├── No progress (same failures) → Try alternative strategy, increment stall counter
  └── Stall counter == 3 OR cycle == max_cycles → EXIT (escalate)
```

**Exit criteria are binary and objective. Never ask the model if it's done — run the tests.**

---

## State / Blackboard Schema

All loop state lives in `healer/state.py` as a typed dataclass. Every cycle reads and writes this.

```python
@dataclass
class HealerState:
    goal: str                        # Original user goal
    target_repo: str                 # Path to repo being healed
    test_command: str                # e.g. "pytest", "npm test"
    cycle: int                       # Current loop iteration
    max_cycles: int                  # Hard cap (default: 10)
    status: Literal["in_progress", "success", "escalated"]
    failures_by_cycle: list[dict]    # Full test output per cycle
    patches_applied: list[dict]      # File, diff, rationale per patch
    stall_counter: int               # Cycles with no improvement
    error_fingerprints: set[str]     # Hashed error signatures seen
```

**Do not store full file contents in state. Store paths and diffs only.**

---

## Agent Roles

| Agent | File | Responsibility |
|---|---|---|
| Diagnoser | `agents/diagnoser.py` | Parse test output → identify root cause → return structured diagnosis |
| Fixer | `agents/fixer.py` | Take diagnosis → produce minimal patch → output unified diff |
| Reviewer | `agents/reviewer.py` | Review patch for correctness and scope before it's applied |
| Loop Controller | `loop.py` | Orchestrates all agents, manages state, enforces exit criteria |

Each agent is a separate function/class. The loop controller is the only thing that reads/writes state.
Agents receive only what they need — not the full state object.

---

## Tool Rules

- `runner.py`: Always capture both stdout and stderr. Return exit code + combined output. Never suppress errors.
- `patcher.py`: Apply patches atomically. On failure, roll back immediately. Log every patch attempt.
- `git.py`: Commit after each successful cycle with message `[healer] cycle-N: <short rationale>`. This creates rollback points.

**Before applying any patch: git commit the current state. If the patch breaks things, rollback is one command.**

---

## Critical Rules

- **Verification is always external.** Run the actual test suite. Never ask the model if the fix worked.
- **Reviewer must approve before patcher applies.** No exceptions. Reviewer sees the diff, not the full file.
- **Scope is sacred.** The fixer only modifies files relevant to the failing test. If it tries to touch unrelated files, the reviewer blocks it.
- **Stall detection is mandatory.** Hash each cycle's failure fingerprint. If the same fingerprint appears twice in a row, switch strategy before retrying.
- **Max cycles is a hard cap.** When hit, call `escalation.py` and exit. Do not extend the cap mid-run.
- **No silent failures.** Every tool call must log its return value. If a tool returns unexpected output, log and escalate — do not ignore.

---

## Escalation Report Format

When the loop exits without success, `escalation.py` outputs a structured Markdown report:

```markdown
# Healer Escalation Report

**Target:** ./path/to/repo
**Cycles Run:** 8 / 10
**Exit Reason:** stall (same failures for 3 consecutive cycles)

## Failing Tests at Exit
- test_user_auth.py::test_login_invalid_password
- test_user_auth.py::test_session_expiry

## What Was Tried
1. Cycle 2: Fixed missing import in auth.py → resolved 3/5 failures
2. Cycle 4: Patched token validation logic → no improvement
3. Cycle 6-8: Attempted 3 alternative approaches → no improvement

## Root Cause Hypothesis
The diagnoser consistently identifies a mismatch between the JWT secret in
config.py and the one loaded at runtime from environment variables.
This cannot be resolved by code changes alone — requires environment configuration.

## Recommended Human Action
Verify JWT_SECRET env var matches the value in test fixtures.
See: tests/fixtures/auth_fixtures.py line 14
```

---

## What NOT to Do

- Do not put business logic in `loop.py`. It orchestrates — it doesn't diagnose or fix.
- Do not pass the full conversation history between cycles. Use the state blackboard.
- Do not let the fixer see the reviewer's internal scoring. Fixer gets: task + relevant files only.
- Do not auto-extend `max_cycles` at runtime. If 10 cycles isn't enough, escalate and let the human decide.
- Do not commit broken state. Only `git.py` commits, and only after `runner.py` confirms improvement.

---

## Docs References

For anything beyond session essentials:
- `@docs/architecture.md` — full system design and agent interaction diagram
- `@docs/loop-design.md` — loop state machine, stall detection logic, strategy switching
- `@docs/escalation-schema.md` — full escalation report JSON schema