# Escalation Schema

## Markdown Report

Written to `<target_repo>/HEALER_ESCALATION.md` when the loop exits without success.

```markdown
# Healer Escalation Report

**Target:** <target_repo path>
**Cycles Run:** <N> / <max_cycles>
**Exit Reason:** <reason string>
**Timestamp:** <ISO 8601 UTC>

## Failing Tests at Exit
- <test_id>
- ...

## What Was Tried
1. Cycle <N>: [<file>] <rationale> → <delta>
2. ...

## Root Cause Hypothesis
<paragraph>

## Recommended Human Action
<paragraph>
```

## Exit Reason Values

| Value | Trigger |
|-------|---------|
| `max_cycles (N) reached` | `cycle >= max_cycles` after OBSERVE |
| `stall (same failures for 3 consecutive cycles)` | `stall_counter >= 3` |

## State JSON Schema

The `--state-out` flag writes final state as JSON. Schema:

```json
{
  "goal": "string",
  "target_repo": "string",
  "test_command": "string",
  "cycle": "integer",
  "max_cycles": "integer",
  "status": "in_progress | success | escalated",
  "failures_by_cycle": [
    {
      "cycle": "integer",
      "exit_code": "integer",
      "failures": ["string"],
      "fingerprint": "string (16-char hex)",
      "output_snippet": "string (first 2000 chars)"
    }
  ],
  "patches_applied": [
    {
      "cycle": "integer",
      "file": "string",
      "diff": "string",
      "rationale": "string"
    }
  ],
  "stall_counter": "integer",
  "error_fingerprints": ["string"]
}
```

## Rationale Prefixes

Patch rationale strings use prefixes to indicate outcome:

| Prefix | Meaning |
|--------|---------|
| *(none)* | Applied and improved test results |
| `REJECTED: ` | Reviewer blocked — not applied |
| `APPLY_FAILED: ` | `patch` command failed — rolled back |
| `NO_PROGRESS: ` | Applied but no improvement — rolled back |
