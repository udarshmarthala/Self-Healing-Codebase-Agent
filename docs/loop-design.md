# Loop Design

## State Machine

```
         ┌──────────────────────────────────────────────┐
         │                  IN_PROGRESS                  │
         │                                               │
         │   OBSERVE → REASON → ACT → VERIFY             │
         │      ↑                        │               │
         │      └──── progress made ─────┘               │
         │                                               │
         │   Exit conditions checked after OBSERVE:      │
         │   - all tests pass → SUCCESS                  │
         │   - at max_cycles → ESCALATED                 │
         │   - stall_counter >= 3 → ESCALATED            │
         └──────────────────────────────────────────────┘
```

## Phase Details

### OBSERVE
1. Call `run_tests(test_command, target_repo)`
2. Parse `failures` from output
3. If `exit_code == 0`: return `SUCCESS`
4. Call `state.record_cycle_result(output, exit_code, failures)` → fingerprint
5. Check `state.is_at_max_cycles()` → escalate if true
6. Check `state.is_stalled(fingerprint)` → escalate if true

### REASON
1. Build `previous_diagnoses` from `state.patches_applied`
2. Call `diagnose(output, failures, target_repo, previous_diagnoses)`
3. Diagnoser classifies error type, extracts affected files, picks strategy

### ACT
1. Snapshot: `sha_before = git.current_sha(target_repo)`
2. Call `generate_fix(diagnosis, target_repo, cycle)` → `Patch`
3. Call `review_patch(patch, diagnosis)` → `ReviewResult`
4. If not approved: record rejected patch, `continue` (back to OBSERVE next cycle)
5. Apply: `patcher.apply_patch(diff)` or `patcher.write_file(path, content)`
6. On `PatchError`: `git.rollback_to(sha_before)`, record failure, `continue`

### VERIFY
1. Re-run `run_tests()` → `verify`
2. Compare `len(verify.failures)` vs `len(result.failures)` from OBSERVE
3. If improved (fewer failures OR exit_code==0):
   - `git.commit(f"[healer] cycle-{N}: {rationale}")`
   - `state.record_patch(file, diff, rationale)`
4. If no improvement:
   - `git.rollback_to(sha_before)`
   - Record as `NO_PROGRESS`

## Stall Detection

Every cycle's failure list is hashed (SHA-256, first 16 chars) into a fingerprint.

```python
def is_stalled(self, fingerprint: str) -> bool:
    if fingerprint in self.error_fingerprints:
        self.stall_counter += 1
    else:
        self.stall_counter = 0
        self.error_fingerprints.add(fingerprint)
    return self.stall_counter >= 3
```

When stalled, the diagnoser's `_pick_strategy` skips strategies already tried (tracked via `patches_applied[*].rationale`).

## Strategy Switching

`diagnoser._pick_strategy` maintains a priority list per error type:

```python
"assertion_error": ["fix_logic", "update_expected_value", "check_data_flow"],
```

On each call, it excludes strategies seen in `previous_diagnoses`. This ensures the fixer tries a different approach after a stall.

## Commit Policy

Git commits happen only after verified improvement:
- Before patch: snapshot SHA
- After patch apply: re-run tests
- If improved: `git commit -m "[healer] cycle-N: <rationale>"`
- If not improved: `git reset --hard <sha_before>`

This creates clean rollback points and ensures the git history only contains progress.
