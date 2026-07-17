# Architecture

## System Overview

The Self-Healing Codebase Agent is a CLI tool that runs an autonomous observe→reason→act→verify loop against a target repository until all tests pass or the loop exhausts its budget.

```
┌─────────────────────────────────────────────────────────┐
│                     loop.py (controller)                 │
│                                                          │
│  ┌────────┐   ┌────────────┐   ┌───────┐   ┌────────┐  │
│  │OBSERVE │ → │   REASON   │ → │  ACT  │ → │VERIFY  │  │
│  │runner  │   │ diagnoser  │   │fixer  │   │runner  │  │
│  │        │   │            │   │review │   │        │  │
│  └────────┘   └────────────┘   │patcher│   └────────┘  │
│                                └───────┘                 │
│                    ↕ state.py (blackboard)               │
└─────────────────────────────────────────────────────────┘
                         │
                    escalation.py
```

## Components

### loop.py — Controller
Orchestrates the four-phase cycle. Reads/writes `HealerState`. The only component allowed to touch state. Enforces exit criteria.

### state.py — Blackboard
Typed dataclass. All inter-cycle memory lives here. Passed by reference; agents receive only slices they need.

### agents/diagnoser.py
- Input: raw test output + failure list + previous diagnoses
- Output: `Diagnosis` (error type, affected files, root cause, strategy)
- Does NOT call LLM — uses regex + heuristics for classification, strategy selection

### agents/fixer.py
- Input: `Diagnosis` + file contents of affected files
- Output: `Patch` (unified diff or full file replacement + rationale)
- Calls Claude API with a tightly scoped prompt

### agents/reviewer.py
- Input: `Patch` + `Diagnosis`
- Output: `ReviewResult` (approved bool + score + reason)
- Two-gate: scope guard (no LLM) + LLM review
- Reviewer never sees the full conversation history

### tools/runner.py
- Runs test command via subprocess
- Captures stdout + stderr combined
- Parses pytest/jest/generic failure lists
- Returns `RunResult`

### tools/patcher.py
- Applies unified diffs via `patch -p1`
- Writes full file content when diff not applicable
- Atomic: any failure raises `PatchError` (caller must rollback)

### tools/git.py
- Thin wrapper over subprocess git
- `commit`, `diff`, `current_sha`, `rollback_to`, `ensure_git_repo`
- Only called from loop.py — agents never touch git directly

### escalation.py
- Generates Markdown report from final state
- Written to `<target_repo>/HEALER_ESCALATION.md`

## Data Flow

```
run_tests() → RunResult
    → diagnose(output, failures) → Diagnosis
        → generate_fix(diagnosis) → Patch
            → review_patch(patch, diagnosis) → ReviewResult
                → [if approved] apply_patch() or write_file()
                    → run_tests() → RunResult (verify)
                        → [if improved] git.commit()
                        → [if no improvement] git.rollback_to(sha_before)
```

## Isolation Guarantees

- Agents receive only the data they need — not the full `HealerState`
- Fixer never sees reviewer scores
- Reviewer sees the diff only, not the full file
- git operations happen only in loop.py, never inside agents
