# SaveState v3.5 for Claude Code

SaveState is a transactional, human-readable project continuity system for Claude Code.

It connects:

- the user's evolving project vision;
- long-term stages;
- short-term phases for the active stage;
- immediate next-session actions;
- technical repository evidence;
- successful approaches;
- failed attempts and corrections;
- immutable session snapshots and Git history.

Its primary purpose is simple:

> Load the current state, understand exactly where work stopped, and continue without losing goals, unresolved plans, prior evidence, or lessons.

## What v3 improves

SaveState v3 combines the strongest parts of two independently developed implementations while correcting their failure modes.

- Sparse saves merge into the previous rolling handoff instead of replacing it.
- Unreached targets survive until explicitly resolved.
- `save-prep → one JSON delta → apply` keeps normal saves efficient.
- Applies are genuinely transactional.
- `--dry-run` writes nothing.
- Locking and revision checks prevent silent concurrent overwrites.
- Structured canonical JSON avoids fragile Markdown parsing.
- Markdown remains the readable project record.
- Targeted retrieval avoids loading the entire vision or history.
- Normal Git commits preserve hooks and include only `.savestate/`.
- SessionStart uses a small pointer by default.

v3.2 answers the deepest structural risk: the state engine itself. Every save now passes an independent conservation audit before it may touch disk, every commit is verified after landing with automatic restore on failure, and every applied delta is journaled verbatim for replay. The suite attacks the engine directly with fault injection, seeded bugs, and randomized fuzzing.

v3.1 hardened the engine: silent no-op resolutions are rejected, blocked targets stay visible in the handoff, save cost no longer grows with archive count, session-boundary detection warns loudly when the session ID is missing, crashed operations are detected and recoverable through `doctor`, and runtime artifacts are kept out of Git automatically.

## Install

Clone into the project's skills directory:

```bash
git clone https://github.com/tylerzonk/save-state.git \
  <project-root>/.claude/skills/save-state
```

Or copy the complete `save-state` directory into the project yourself:

```text
<project-root>/.claude/skills/save-state/
```

The skill entrypoint must be:

```text
<project-root>/.claude/skills/save-state/SKILL.md
```

Start Claude Code from the project root and say:

```text
Make a SaveState for this project.
```

or invoke:

```text
/save-state save
```

The first save creates:

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
├── stages/
├── logs/
└── history/
```

## Typical workflow

### Start a session

```text
What is the current state of the project?
```

Claude returns:

1. Active project path
2. Last session
3. Immediate resume point
4. Session targets
5. Relevant recent issue, when useful

It waits for approval rather than starting automatically.

### Continue

```text
Continue with the plan.
```

### Save

```text
Save the current state.
```

Repeated saves in the same Claude session update the same logical SaveState cycle. The first save from a later Claude session archives the previous cycle exactly once.

### Capture one exact idea cheaply

```text
Add this to SaveState: community chat and direct messages may share a messaging foundation.
```

This becomes a `NOTE-###` in `PENDING.md` and is reconciled during a later full save.

### Explicit checkpoint

```text
Create a SaveState checkpoint before this refactor.
```

### Explicit closeout

```text
Close out this SaveState cycle and start a new one.
```

## Goal hierarchy

```text
Project Vision
└── Long-term Stages
    └── Short-term Phases for the active stage only
        ├── Milestone criteria
        └── Immediate Actions
```

### Project Vision

The vision is a detailed repository of the intended finished project, not merely a slogan. It can contain specific feature behavior, design concepts, constraints, examples, and uncertain ideas.

Vision maturity:

- Confirmed
- Developing
- Unrefined

Maturity describes development, not permanence. Retired ideas remain available as parked, rejected, or superseded history.

### Long-term stages

Stages are SMART-guided major outcomes. Future stages remain descriptive so the project is not over-planned before new information exists.

### Short-term phases

Only the active stage is decomposed into phases. A phase file begins when the phase first becomes active and grows into the durable completion record.

### Immediate actions

Immediate actions are the exact resume point and concrete session targets. They are the strictest and most executable part of the system.

## Rolling current state

`CURRENT.md` is a magnifying-glass view of the present, not a chronological dump.

```text
previous CURRENT
+ verified work and decisions from this session
- resolved or obsolete details
= new CURRENT
```

Relevant unresolved context can survive several sessions. Completed detail moves into phase history, lessons, logs, snapshots, and Git.

## Context efficiency

The system uses progressive disclosure:

- **Hot:** `CURRENT.md`
- **Warm:** active phase, vision, goals, technical state, lessons, index
- **Cold:** logs, completed phases, snapshots, Git

Routine operation is usually:

1. `save-prep`
2. one structured delta
3. one transactional `apply`

Indexing, rendering, validation, snapshots, and Git operations happen locally in Python.

## Optional SessionStart hook

Merge `settings-snippet.json` into the project's `.claude/settings.json`.

Default mode is `pointer`, which injects only the active stage, phase, resume point, and path to `CURRENT.md`.

Configure `.savestate/config.json`:

```json
{
  "briefing": {
    "session_start": "pointer"
  }
}
```

Allowed values:

- `off`
- `pointer`
- `full`

## Direct helper commands

```bash
PYTHON="$(command -v python3 || command -v python)"
SS="$PYTHON .claude/skills/save-state/scripts/state.py"

$SS load --root "$PWD" --lite
$SS save-prep --root "$PWD" --session-id "$CLAUDE_SESSION_ID"
$SS apply --root "$PWD" --session-id "$CLAUDE_SESSION_ID" delta.json
$SS note --root "$PWD" --text "exact note"
$SS vision --root "$PWD" --tags messaging identity
$SS phase --root "$PWD" PH-007
$SS find --root "$PWD" --file src/ranking.py
$SS history --root "$PWD"
$SS doctor --root "$PWD"
```

## Git policy

Default state commits contain only `.savestate/`.

SaveState does not:

- commit unfinished project code;
- include unrelated staged files;
- bypass Git hooks;
- disable signing or repository policy.

Disable automatic state commits:

```json
{
  "git": {
    "auto_commit_state": false
  }
}
```

## Test

From the skill directory:

```bash
python3 -m unittest discover -s tests -v
```

The included 65-test suite (including the growth gauge, which asserts load costs stay flat across a simulated multi-stage project) covers rolling merges, transaction rollback, true dry runs, stale revisions, session archives, notes, vision approval, future-stage planning limits, phase lifecycle, drift repair, snapshots, Git isolation, Git hooks, worktrees, and the v3.1 regression cases: exact-text resolution, blocked-target visibility, inline phase completion records, empty-session boundary warnings, orphan detection, history exclusion from transactions, snapshot slimming, gitignore management, and dead-lock reclamation.

## License

MIT. See [LICENSE](LICENSE).
