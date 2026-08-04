# SaveState workflows

Read this when defining or changing goals, completing phases, handling a
direction change, or distilling an oversized document. Routine handoff saves
do not need it.

## Goal model

```text
Project Vision
└── Long-term Stages
    └── Short-term Phases for the active stage only
        ├── Milestone criteria
        └── Immediate Actions
```

- **Project Vision:** detailed repository of the intended finished project, including specific features, behaviors, concepts, constraints, and uncertain ideas. Intentionally broader and more abstract than a SMART goal.
- **Long-term Stages:** SMART-guided major outcomes required to realize the vision. Future stages stay descriptive and intentionally undecomposed.
- **Short-term Phases:** concrete work for the active stage only. A planned phase gets a standalone file when it first becomes active.
- **Milestones:** measurable exit criteria inside a phase, not another hierarchy level.
- **Immediate Actions:** the exact resume point and next executable session targets. SMART strictness increases toward this level.

IDs are globally unique: `VIS-###`, `STG-###`, `PH-###`, `ACT-###`, `LES-###`, `EVT-###`, `NOTE-###`, `SSC-###`. They continue past 999 without changing meaning.

## Vision maturity and approval

All vision content lives in `VISION.md` under three maturity levels. Maturity describes development, not permanence or importance; disposition (active, parked, rejected, superseded) is tracked separately.

- **Confirmed:** explicitly adopted. Meaningfully rewriting, demoting, retiring, parking, or superseding a confirmed item requires explaining the impact and passing `user_approved: true` after the user agrees.
- **Developing:** meaningfully explored and actively being refined.
- **Unrefined:** captured early idea awaiting development.

Never silently reintroduce a rejected or superseded idea; reactivation always requires fresh user approval. Before defining or materially changing a stage or phase, retrieve adjacent vision items by tag or relationship and surface developing or unrefined ideas as proposals, never as automatic additions. Preserve the user's exact wording in `user_words`.

## Phase lifecycle and completion

- Define phases only for the active stage; do not over-plan future stages.
- Statuses: planned, active, paused, blocked, completed, superseded, cancelled. At most one stage and one phase are active.
- A paused or blocked phase stays visible with its reason and resume condition; blocked targets stay visible in `CURRENT.md`.
- Completing a phase or stage requires nonempty evidence or an explicit `unverified: true`, which should be rare and visible.
- A completed phase file is the durable account: intended outcome, how the work evolved, implementation and evidence, failed attempts and corrections, files and commits, reusable lessons, follow-on impact. Preserve successful methods as carefully as failures.
- Reuse an existing phase ID when the same objective and completion criteria return; create a new linked ID when the objective materially changed.
- When paused work turns out to belong to a different stage, `move_phase` re-parents it with its whole history intact, including into a planned future stage. Superseding requires the replacement to already exist, so supersede after creating it, never before.

## Direction changes

1. Preserve the user's exact wording.
2. Clarify only ambiguity that genuinely affects architecture, scope, security, completion criteria, or reuse of existing work.
3. Perform impact analysis: unchanged, reusable, superseded, newly required, affected descendants.
4. Retire old goals with a reason, replacement, reusable work, and reconsideration condition.
5. Create the new vision, stage, phase, and immediate actions only once intent is clear enough. Use `--dry-run` first for strategic changes.

## Checkpoint and closeout

- `checkpoint` creates an additional immutable snapshot inside the same cycle.
- `closeout` archives the current cycle and opens a new logical cycle, even inside one conversation. Save unsaved semantic work first.
- Loading never archives. Repeated saves in one Claude session update the same cycle; the first save of a later session archives the prior cycle exactly once.

## Budget distillation

When a document approaches its budget, or a save is rejected at the hard cap, distill in the same delta: complete, cancel, or supersede finished targets; resolve stale issues and threads; replace `last_session` with a shorter summary; move chronology into the active phase file and lessons. Keep the consequence in `CURRENT.md` and the full story in phase files, logs, lessons, snapshots, and Git. Do not discard evidence merely to reduce size.
