# Design decisions behind SaveState v3

## Adopted from the earlier SaveState design

- `CURRENT.md` as a rolling working note and magnifying-glass view of now
- detailed project vision separate from SMART-guided stages and phases
- active-stage-only phase decomposition
- phase files as human-readable completion history
- comprehensive snapshots and conservative normal Git behavior
- exact resume point as the primary feature

## Adopted from the alternative implementation

- `save-prep → structured delta → apply`
- targeted retrieval commands
- compact SessionStart pointer
- explicit delta operation schema
- per-document context budgets
- doctor and repair workflow
- narrow Claude Code tool permissions
- exact preservation of user wording

## Deliberately rejected or corrected

- sparse handoff replacement that could erase prior context
- a dry run that performed live writes
- partial writes before validation completed
- all-or-nothing pending-note clearing
- idle-time guesses for session boundaries
- bypassing Git hooks with direct commit-tree operations
- fragile Markdown parsing as the machine database
- claiming a full index rebuild while reparsing only part of the state
- nested ZIP packaging

## Result

SaveState v3 uses structured canonical data for correctness and generated Markdown for readability. Transactions, locks, revisions, targeted retrieval, normal Git commits, and explicit lifecycle operations reinforce the original goal: reliable continuity without context flooding or silent forgetting.
