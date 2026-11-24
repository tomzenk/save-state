# Changelog

## 3.5.0

Friction fixes found in real use (a nine-stage data pipeline build, about
twenty saves in one conversation with no session ID in the shell):

- `save-prep` now returns `open_target_ids` as bare `ACT-###` strings, so
  they feed `complete_target` directly. The `{id, text}` objects moved to a
  new `open_targets` field. Previously the objects sat under the `_ids` name
  and passing one to `complete_target` failed with "Unknown action target".
- Added `state.py revision`, which prints the current revision without the
  full `save-prep` payload. A stale-delta rejection now names the usual
  causes (a note, checkpoint, `doctor --repair`, or another session's save)
  and points at the new command.
- The first save after `init` no longer opens a second cycle. A cycle that
  has had no saves yet is reused when a delta asks for `boundary: new`
  (with a warning), and `save-prep` recommends `same` for it. Metadata now
  tracks `cycle_applies`; states from before 3.5 lack it and keep the old
  behavior.
- `doctor` warns about commit hashes recorded on phases that no longer exist
  in the repository, which happens after a history rewrite.

## 3.4.0

Refinements from a full project simulation (a solo Reddit-scale build run
through 25 messy sessions: pivots, blocked dependencies, a reopened phase,
racing terminals, and realistic operator mistakes):

- Superseding a phase or vision item with a `by` reference that does not
  exist is now rejected with guidance, instead of silently recording a
  replacement pointing at nothing.
- Added `move_phase`: re-parents existing work to another stage with its
  full history intact, including into a planned future stage. It refuses to
  move an active phase or target a retired stage. This closes the gap that
  previously forced an impossible supersede-then-create ordering when
  paused work belonged to a later stage.
- `resolve_issue` and `resolve_thread` accept a whitespace-normalized match
  when it is unique, so multiline entries can be resolved from their
  rendered form; ambiguous matches are still rejected, and the auditor
  authorizes exactly the normalized resolution.
- The routine cheat sheet now includes `decisions` and `lessons`, which the
  simulation showed arise every few sessions; SCHEMA.md remains needed only
  for vision and goal surgery.
- A reopened phase labels its earlier completion as a prior record instead
  of rendering it as current.

Everything else held under the simulation: all guardrail rejections fired
(future-stage over-planning, stale second terminal, unapproved reactivation
of a rejected idea, stage completion over a paused phase, budget hard cap),
the journal accounted for every apply, Git stayed clean, doctor stayed
green, and loads stayed flat at 137/452/865 tokens after 25 sessions.


## 3.3.0

The context diet and growth control, driven by measurement:

- SKILL.md restructured into a 1,425-token router (from 3,558): activation
  keeps the load workflow, the save transaction, a routine delta cheat
  sheet, boundary guidance, and retrieval commands; the goal model, vision
  rules, phase lifecycle, direction changes, and distillation guidance
  moved to references/WORKFLOWS.md, read when those situations arise.
- Routine saves are schema-free: the inlined cheat sheet covers handoff
  operations, narrative evidence, and note resolution, so SCHEMA.md is read
  only for vision and goal surgery.
- save-prep returns a prefilled delta_skeleton plus open_target_ids and
  pending_note_ids, and echoes the active stage and phase as summaries with
  counts instead of full records (--full restores the old echo). Save-prep
  no longer grows with phase age.
- Boundary hygiene rotates the hot file when a new cycle opens:
  recent_phase_changes keeps its last five entries and last_session
  collapses into a single carried line, with full history preserved in the
  boundary snapshot, phase records, and the event log. The conservation
  auditor authorizes exactly this rotation and nothing else.
- INDEX.md replaced by a capped orientation card (263 tokens at stage 11,
  from 11,762): category counts, retrieval commands, and the five most
  recent phases, lessons, and vision items. Machine lookup still uses
  data/index.json through find and vision.
- Added the growth gauge (tests/test_growth_gauge.py): builds a multi-stage
  project in miniature and asserts session-start and save-time costs at the
  final stage stay within a fixed band of stage one, with absolute
  ceilings. Flatness is now a tested property.

Measured on an 11-stage, 41-phase, 184-session simulation: CURRENT.md
445 tokens at stage 11 (was 812 and climbing), save-prep 776 (was 2,058),
full resume-plus-save session overhead about 2,880 tokens (was about
8,700). Requests unchanged: one call to resume, two to save.


## 3.2.0

The single-point-of-failure answer. The engine remains the only component
that transforms state, but it can no longer act alone:

- Added an independent conservation auditor (`invariants.py`) that runs on
  every apply. It computes the change surface the delta authorizes and
  rejects the save when anything outside that surface changed. Seeded-bug
  tests prove it catches a resurrected handoff-erasure defect and silent
  target loss before either reaches disk.
- Fixed a catastrophic defect found by crash injection: when the first
  rename of a commit failed, the old rollback handler deleted the entire
  live `.savestate/` with no backup in existence. The rewritten commit
  protocol is phase-aware and never deletes the live state on any path.
- Commits are now verified after landing. The pre-commit backup is held
  until the new state proves loadable, valid, and drift-free; verification
  failure restores the previous state automatically.
- Every applied delta is journaled verbatim with pre-state content hashes
  under `history/journal/`, written by a small mechanical function outside
  the semantic engine paths. `history --journal` lists entries. Dry runs
  and rejected saves journal nothing.
- `cli` now reports all filesystem errors (disk full, permissions) as
  structured JSON errors instead of raw tracebacks.
- Budget hard-cap errors explain exactly how to distill the oversized file
  in the same delta.
- The note command documents and tests the stdin form, which accepts
  wording that begins with a dash.
- Frontmatter values are sanitized against newline injection.
- Added the hardening suite: fault injection at every filesystem mutation
  point in an apply, both rename failure phases, simulated post-commit
  corruption, seeded engine bugs versus the auditor, journal coverage, and
  bounded randomized conservation fuzzing with an independent shadow model
  (57 tests total; SAVESTATE_FUZZ_STEPS raises fuzz depth).


## 3.1.0

Correctness:

- `resolve_issue` and `resolve_thread` now reject non-matching text with the current entries listed, instead of silently succeeding while removing nothing.
- Blocked session targets render in `CURRENT.md` under a dedicated heading with their blocking reason, and `load --lite` returns them separately, so held work stays visible in the handoff.
- Completing a phase that was never activated now stamps activation and produces the durable standalone phase file.
- `add_target` operations no longer leak the `op` key or other extraneous fields into stored targets.
- `save-prep` and `apply` warn explicitly when the Claude session ID is empty, and the skill instructs Claude to pass the boundary itself in that case, so archiving cannot silently stop.
- `doctor` detects orphaned `.savestate.backup-*` directories, leftover transaction directories, and lingering locks, and prints the exact restore command when the live state is missing.
- `doctor --repair` only bumps the revision when drift actually existed.

Performance and hygiene:

- Transactions hardlink the immutable `history/` tree instead of byte-copying it, and transaction diffs exclude it, so save cost no longer grows with the archive count.
- Session snapshots exclude the ever-growing event log by default (`snapshot.include_events` restores the old behavior); events remain in live data and in Git history.
- Snapshot working-set selection copies the active stage directory precisely instead of substring-matching file contents.
- Duplicate-ID validation is linear instead of quadratic.
- Init and the first apply extend the project `.gitignore` idempotently so the lock, transaction, and backup artifacts are never committed by a stray `git add .`.
- Lock staleness checks the holder PID, so a crashed save no longer blocks the next save for an hour.
- The hook entrypoint no longer blocks when run manually on a terminal.
- Generated Markdown and helper output use plain hyphens rather than em dashes.
- `allowed-tools` uses the documented string form with command-scoped Bash patterns.


## 3.0.0

- Rebuilt saves around `save-prep → sparse delta → transactional apply`.
- Added canonical structured JSON and generated human-readable Markdown.
- Added rolling handoff merge semantics so omitted fields are preserved.
- Added explicit action lifecycle operations for unreached session targets.
- Added true dry-run behavior.
- Added lock files and optimistic revision checks.
- Added targeted vision, phase, event, tag, file, text, and history retrieval.
- Added compact SessionStart pointer mode.
- Added vision maturity and retired-idea approval rules.
- Enforced active-stage-only phase decomposition.
- Added paused and blocked phase resume conditions.
- Added evidence-backed completed phase histories.
- Added exact pending-note IDs and partial reconciliation.
- Added deterministic doctor and repair from canonical data.
- Added state-only normal Git commits that preserve hooks and unrelated staged work.
- Added Git worktree root detection through `git rev-parse`.
- Added comprehensive transaction, drift, lifecycle, Git, and concurrency tests.
