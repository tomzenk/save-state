---
name: save-state
description: Preserve and resume a software project's rolling current state by connecting the user's detailed vision, long-term stages, active-stage phases, immediate actions, technical evidence, successful approaches, failed attempts, and corrections. Use when the user asks to save state, load or check the current project state, see where work left off, add a note to SaveState, checkpoint or close out a state cycle, or persist a project-direction change.
argument-hint: "[load|save|note|checkpoint|closeout|doctor] [details]"
allowed-tools: Read, Grep, Glob, Bash(python3:*), Bash(python:*), Bash(git status:*), Bash(git log:*), Bash(git diff:*), Bash(git rev-parse:*)
---

# SaveState

Goal-to-execution continuity: load the compact rolling handoff, brief the user, work after they approve, then save the session delta without losing unresolved prior context.

Current Claude session: `${CLAUDE_SESSION_ID}`

## Setup

```bash
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PYTHON="$(command -v python3 || command -v python)"
SS="$PYTHON ${CLAUDE_SKILL_DIR}/scripts/state.py"
```

State lives in `.savestate/`. JSON under `.savestate/data/` is the machine source of truth; Markdown files are generated views, so never rewrite them directly. `CURRENT.md` is the hot handoff.

## Load, check, or where we left off

Loading is read-only; it must not initialize, archive, or commit anything.

1. `$SS load --root "$PROJECT_ROOT" --lite`
2. Read `.savestate/CURRENT.md` only if the compact result is insufficient, and the active phase file only when the handoff points at unresolved detail.
3. Brief the user in this order: active project path; last session; immediate resume point; open session targets (mention blocked ones with their reasons); one relevant recent issue when useful.
4. Do not begin work. Wait for the user to approve, redirect, or ask. Ordinary language like "continue" then starts work; if no briefing exists yet in the conversation, brief first.

## Save

A save is one semantic transaction: `save-prep`, one JSON delta, one `apply`.

```bash
$SS save-prep --root "$PROJECT_ROOT" --session-id "${CLAUDE_SESSION_ID}"
cat <<'JSON' | $SS apply --root "$PROJECT_ROOT" --session-id "${CLAUDE_SESSION_ID}" -
{ ...delta... }
JSON
```

`save-prep` returns `expected_revision`, a prefilled `delta_skeleton`, the previous handoff, active stage and phase summaries, `open_target_ids` (bare IDs for `complete_target`) with `open_targets` (ID and text), pending note IDs, Git context, and warnings. When only the revision is needed, `$SS revision` is the cheap lookup. If `session_warnings` reports an empty session ID, decide the boundary yourself: pass `"boundary": "new"` on your first save of this conversation and `"same"` afterward; never let a conversation end unarchived because the ID was unavailable.

Reconcile before writing: `CURRENT.md` is an advancing working note, so every prior item ends up carried forward, progressed, completed, blocked, superseded, or resolved. Never silently drop an unreached target, an unresolved blocker, a live warning, or a still-correct resume point. Omitted fields are preserved automatically; resolve or replace things explicitly. Verify evidence (branch, diff, tests, observed results) before claiming completion.

### Routine delta cheat sheet

Most saves need only these fields; all are optional except `expected_revision`:

```json
{"expected_revision": 0,
 "session_summary": "short commit subject",
 "handoff": {"last_session": ["what happened"],
             "resume_point": {"action": "", "location": "", "start_with": "", "expected": "", "verify": ""}},
 "handoff_ops": [
   {"op": "complete_target", "id": "ACT-000", "result": ""},
   {"op": "add_target", "text": ""},
   {"op": "carry_forward_target", "id": "ACT-000"},
   {"op": "block_target", "id": "ACT-000", "reason": ""},
   {"op": "add_issue", "text": ""},
   {"op": "resolve_issue", "text": "exact existing text or the op is rejected"},
   {"op": "append_last_session", "text": ""}],
 "accomplished": [{"what": "", "how": "", "evidence": "", "files": []}],
 "failed": [{"attempt": "", "why": "", "correction": ""}],
 "decisions": [{"what": "", "why": ""}],
 "lessons": [{"kind": "success", "summary": "", "detail": ""}],
 "resolve_notes": ["NOTE-000"],
 "git": {"commit": true}}
```

For anything beyond the cheat sheet, `vision_ops`, `goal_ops` (stages and phases), or `technical`, read `references/SCHEMA.md` first.

The engine applies transactionally: an independent conservation auditor rejects any change the delta does not authorize, the commit is verified after landing with automatic restore on failure, and every applied delta is journaled under `.savestate/history/journal/`. A rejected save changes nothing. Use `--dry-run` before risky direction changes. Report only what changed, warnings, the Git result, and the new resume point.

## Quick reference

- Exact lightweight note (no full save): `printf '%s' "wording" | $SS note --root "$PROJECT_ROOT" --session-id "${CLAUDE_SESSION_ID}"` (stdin form; `--text` fails on dash-leading wording)
- Retrieval, always targeted: `$SS vision --tags x`, `$SS phase PH-000`, `$SS find --id/--file/--tag/--text ...`, `$SS history [--journal]`
- Current revision only: `$SS revision`
- Health and recovery: `$SS doctor [--repair]`; run it whenever an apply fails twice
- `checkpoint` freezes a snapshot; `closeout` archives the cycle and opens a new one

## Read when the situation arises

- `references/WORKFLOWS.md`: the goal model (vision, stages, phases, actions), vision maturity and approval rules, phase lifecycle and completion evidence, direction changes, checkpoint and closeout semantics, budget distillation
- `references/SCHEMA.md`: full delta field reference for vision, goal, decision, lesson, and technical operations
- `references/OPERATIONS.md`: session boundaries, interrupted-operation recovery, concurrency, drift repair
