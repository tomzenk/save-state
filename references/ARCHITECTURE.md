# SaveState v3 architecture

## Three connected layers

### Human goal layer

What the user is building, why, which concepts belong in the vision, what has changed, and how far the project has advanced.

### Translation layer

Turns natural-language intent into stages, active-stage phases, milestone evidence, and immediate actions. This is SaveState's distinctive layer.

### AI execution-memory layer

Files, commits, commands, tests, current technical truth, successful methods, failed attempts, corrections, and exact resume instructions.

## Hot, warm, and cold context

### Hot

`CURRENT.md` is the magnifying-glass view of now:

- active stage and phase;
- last relevant work;
- recently completed or changed phases;
- exact resume point;
- open session targets;
- current issue or warning;
- repository handoff.

### Warm

Read only when relevant:

- active phase file;
- `VISION.md`;
- `GOALS.md`;
- `TECHNICAL_STATE.md`;
- `LESSONS.md`;
- `INDEX.md`.

### Cold

Retrieve by ID, tag, file, or text:

- event logs;
- frozen cycle snapshots;
- completed historical phases;
- Git history.

## Runtime bundle

```text
.savestate/
├── CURRENT.md
├── VISION.md
├── GOALS.md
├── TECHNICAL_STATE.md
├── LESSONS.md
├── INDEX.md
├── PENDING.md
├── config.json
├── metadata.json
├── data/
│   ├── current.json
│   ├── vision.json
│   ├── stages.json
│   ├── phases.json
│   ├── technical.json
│   ├── lessons.json
│   ├── pending.json
│   ├── events.json
│   └── index.json
├── stages/
│   └── STG-###-slug/
│       ├── STAGE.md
│       └── phases/
│           └── PH-###-slug.md
├── logs/
│   └── YYYY-MM.md
└── history/
    └── SSC-###__timestamp__reason/
```

Structured data prevents fragile Markdown parsing. Generated Markdown preserves the natural-language, human-readable project record.

## Snapshot contents

A snapshot contains:

- all hot and warm core Markdown views;
- canonical structured data;
- config and metadata as they existed at the boundary;
- active stage and phase records;
- a manifest containing cycle, revision, branch, HEAD, worktree, and changed files.

Git supplies exact repository history. The snapshot supplies a frozen natural-language and structured project state.


## Defense in depth on the save path

The engine is the only component that transforms state, which makes it the
component most worth distrusting. Three independent mechanisms bound the
damage any engine defect can cause:

1. **Conservation auditor** (`invariants.py`). A separate module with no
   transformation logic receives the before-state, the delta, and the
   after-state, computes the change surface the delta authorizes, and rejects
   the save when anything outside that surface changed. A silent-forgetting
   bug must now exist in the engine and the auditor simultaneously before it
   can reach disk. Keep `invariants.py` free of engine imports and of any
   code that builds state; it may only read and compare.
2. **Verified commits.** The pre-commit backup is held until the landed state
   is re-read, re-validated, and drift-checked. Verification failure restores
   the previous state automatically. The rename sequence is phase-aware: a
   failure before the live state moves touches nothing, and a failure after
   it moves restores the preserved copy. The live state is never deleted on
   any failure path.
3. **Delta journal.** Every applied delta is written verbatim to
   `history/journal/`, with the pre-state data hashes, by a small mechanical
   function outside the semantic engine paths. If a defect ever slips past
   the first two layers, the journal holds a complete replayable account of
   every save for diagnosis and reconstruction.

The test suite exercises all three adversarially: fault injection at every
filesystem mutation point, deliberately seeded engine bugs that the auditor
must catch, simulated post-commit corruption that verification must roll
back, and randomized conservation fuzzing against an independent shadow
model (`SAVESTATE_FUZZ_STEPS` raises the depth).
