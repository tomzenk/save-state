# SaveState operations and edge cases

## Read-only load

`load` must never initialize or update state. The user can inspect the project and close Claude without changing anything.

## Rolling reconciliation

The prior handoff is the starting point for the next save. Preserve current relevance, not a fixed number of sessions. An unresolved blocker may stay hot for several sessions; a resolved detail leaves `CURRENT.md` but remains in phase history, lessons, logs, snapshots, and Git.

Use the rule:

> Keep the consequence in `CURRENT.md`; keep the full story in the phase file, logs, lessons, and Git.

## New-session boundary

`boundary: auto` uses `${CLAUDE_SESSION_ID}` when available.

- Same session ID: same SaveState cycle.
- Different nonempty session ID: archive the prior cycle once, then open a new cycle.
- No session ID: preserve the existing cycle unless `boundary: new` is explicit. `save-prep` and `apply` both warn in this situation, and Claude must then declare the boundary itself: `new` on the first save of the conversation, `same` afterward.

No idle-time heuristic is used. Time gaps are not reliable session boundaries.

## Boundary hygiene

Opening a new cycle rotates the hot handoff: `recent_phase_changes` keeps
its last five entries and `last_session` collapses into one carried line.
Everything rotated out was archived seconds earlier in the boundary
snapshot and remains permanently in the event log and phase records. The
conservation auditor authorizes this rotation only when the cycle actually
changed.

## Explicit checkpoint

A checkpoint freezes the current bundle but does not open a new cycle.

```bash
state.py checkpoint --root "$PROJECT_ROOT" --label before-auth-refactor
```

## Explicit closeout

Closeout freezes the current cycle and opens a new logical cycle, even inside one Claude conversation.

```bash
state.py closeout --root "$PROJECT_ROOT" --session-id "$CLAUDE_SESSION_ID" --label finished-parser-work
```

Save unsaved semantic work before closeout.

## Transaction and dry run

Every apply occurs in a temporary directory under the project root so it remains on the same filesystem. Validation happens before the live `.savestate/` directory is replaced.

`--dry-run` uses the same transaction and validation path but discards the result. It reports a proposed unified diff and must not modify live files, revisions, cycles, or snapshots.

## Concurrency

The engine uses:

- `.savestate.lock` for mutual exclusion;
- `expected_revision` for optimistic concurrency;
- a second revision check after acquiring the lock.

When stale, rerun `save-prep` and rebuild the delta against the newer state. Never force an old delta over a newer revision.

## Generated Markdown drift

Markdown is generated from structured canonical data. Direct Markdown edits may be overwritten.

```bash
state.py doctor --root "$PROJECT_ROOT"
state.py doctor --root "$PROJECT_ROOT" --repair
```

Repair regenerates Markdown and indexes from canonical JSON. It does not pretend to parse arbitrary Markdown back into structured state.

## Interrupted operations

A crash between the two directory swaps of a commit can leave the live state inside `.savestate.backup-*`. `doctor` detects this and prints the exact restore command. Leftover `.savestate-txn-*` directories are always safe to delete, and a lock whose owning process has died is reclaimed automatically on the next operation.

## Malformed canonical JSON

Malformed JSON is an error. The engine does not silently replace it with empty defaults. Recover from a history snapshot or Git, then run doctor.

## Paused work

Pausing a phase keeps:

- its global ID;
- standalone file if it was ever active;
- accomplishments, failures, decisions, and lessons;
- pause reason;
- resume condition.

When resumed, use the same phase ID unless the objective or success criteria materially changed.

## Reopening versus replacement

Reuse the existing ID when the same objective and completion criteria return. Create a new ID and link it when the objective, purpose, or success evidence changed materially.

## Retired vision ideas

A rejected or superseded idea remains retrievable. Claude may propose reconsideration when new evidence is relevant, but only the user can approve reactivation.

## Completion evidence

A phase cannot be marked completed without evidence unless the delta explicitly says `unverified: true`. Unverified completion should be rare and visible.

## Context budgets

Budgets are checked before transaction commit. Passing twice a configured target is a hard validation error for current, vision, technical state, or a phase file. The live state remains unchanged.

When a document approaches its target:

- distill active consequences into `CURRENT.md`;
- keep detailed chronology in phase records and logs;
- retrieve vision and history by IDs and tags;
- do not discard evidence merely to reduce size.
