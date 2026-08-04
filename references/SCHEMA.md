# SaveState v3 delta schema

A save is one JSON object passed to `state.py apply -`. Every operation is applied in a temporary transaction. Omitted fields are preserved.

## Required top-level field

```json
{"expected_revision": 12}
```

Obtain it from `save-prep`. A mismatched revision is rejected to prevent concurrent or stale sessions from overwriting newer state.

## Common top-level fields

| Field | Purpose |
| --- | --- |
| `expected_revision` | Required optimistic-concurrency revision |
| `session_summary` | Short state-commit subject |
| `boundary` | `auto`, `same`, or `new`; normally use `auto` |
| `cycle_label` | Optional label when a new cycle opens |
| `handoff` | Sparse field-level patch to the rolling current state |
| `handoff_ops` | Explicit action, issue, thread, path, and resume operations |
| `vision_ops` | Changes to vision maturity, content, or disposition |
| `goal_ops` | Stage and phase lifecycle changes |
| `accomplished` | Verified accomplishments and how they succeeded |
| `failed` | Attempts, observed failures, and corrections |
| `decisions` | Decisions and reasoning |
| `lessons` | Reusable successes, failures, corrections, decisions, or preferences |
| `technical` | Sparse technical-state patch |
| `resolve_notes` | Exact `NOTE-###` IDs reconciled by this save |
| `git` | `{"commit": true|false, "message": "..."}` |

## Handoff patch

Only supplied fields change. Unmentioned fields remain exactly as they were.

```json
{
  "handoff": {
    "active_stage": "STG-002",
    "active_phase": "PH-007",
    "last_session": [
      "Corrected ancestry traversal.",
      "Two third-level tests remain unresolved."
    ],
    "resume_point": {
      "action": "Reproduce the two third-level failures",
      "location": "tests/test_ancestry.py",
      "start_with": "pytest tests/test_ancestry.py -k depth_three",
      "expected": "two known failures",
      "verify": "pytest tests/test_ancestry.py"
    },
    "session_targets": [
      "Reproduce the failures",
      "Correct the third-level multiplier",
      "Run the full regression suite"
    ],
    "relevant_issues": [
      "Do not normalize before ancestry weighting."
    ],
    "open_threads": [
      "Container behavior still differs from local behavior."
    ],
    "technical_summary": "Depth-one and depth-two ranking pass; depth three remains incorrect.",
    "boot_sequence": [
      "Run the focused depth-three tests.",
      "Inspect multiplier ordering before changing the tokenizer."
    ]
  }
}
```

Supplying `session_targets` replaces the target collection and allocates IDs for string entries. Prefer operations when only one target changed.

## Handoff operations

```json
{"op": "append_last_session", "text": "Reproduced the failure."}
{"op": "add_target", "text": "Run full regressions"}
{"op": "complete_target", "id": "ACT-014", "result": "40 tests pass"}
{"op": "block_target", "id": "ACT-015", "reason": "awaiting credentials"}
{"op": "cancel_target", "id": "ACT-016", "reason": "no longer relevant"}
{"op": "supersede_target", "id": "ACT-017", "reason": "replaced by ACT-021"}
{"op": "carry_forward_target", "id": "ACT-018", "text": "optional corrected wording"}
{"op": "set_resume_point", "action": "...", "location": "...", "start_with": "...", "expected": "...", "verify": "..."}
{"op": "set_active_path", "stage": "STG-002", "phase": "PH-007"}
{"op": "add_issue", "text": "..."}
{"op": "resolve_issue", "text": "exact existing issue text (a mismatch is rejected, never a silent no-op)"}
{"op": "add_thread", "text": "..."}
{"op": "resolve_thread", "text": "exact existing thread text"}
{"op": "clear_recent_phase_changes"}
```

A target that was not reached remains open until explicitly completed, blocked, cancelled, or superseded.

## Vision operations

### Add

```json
{
  "op": "add",
  "maturity": "unrefined",
  "summary": "Direct messages between users",
  "detail": "Early concept for private communication.",
  "user_words": "Maybe users should be able to message each other directly.",
  "tags": ["messaging", "identity", "notifications"],
  "related": ["VIS-011"],
  "open_questions": ["Should chat and DMs share storage?"]
}
```

Adding a confirmed item requires `"user_approved": true`.

### Update and maturity

```json
{"op": "update", "id": "VIS-018", "detail": "...", "tags": ["messaging"]}
{"op": "promote", "id": "VIS-018", "to": "developing", "reason": "user refined the concept"}
{"op": "promote", "id": "VIS-018", "to": "confirmed", "reason": "user adopted it", "user_approved": true}
{"op": "demote", "id": "VIS-004", "to": "developing", "reason": "user reopened the model", "user_approved": true}
```

Meaningful rewriting of an active confirmed item requires `user_approved=true`.

### Disposition

```json
{"op": "park", "id": "VIS-017", "reason": "revisit after launch"}
{"op": "reject", "id": "VIS-017", "reason": "outside the project scope"}
{"op": "supersede", "id": "VIS-004", "by": "VIS-021", "reason": "new model replaces it"}
{"op": "reactivate", "id": "VIS-017", "reason": "new evidence makes it useful", "user_approved": true}
```

Retiring a confirmed item requires explicit approval. Reactivation always requires explicit approval because retired ideas must be proposed, not silently restored.

### Values

- Maturity: `confirmed`, `developing`, `unrefined`
- Disposition: `active`, `parked`, `rejected`, `superseded`

## Goal operations

### Stages

```json
{
  "op": "add_stage",
  "title": "Build the ranking system",
  "intent": "Posts propagate through community ancestry with decreasing relevance.",
  "criteria": ["ancestry traversal works", "distance weighting is validated"],
  "vision": ["VIS-002"],
  "status": "active"
}
```

```json
{"op": "set_stage_status", "id": "STG-002", "status": "paused", "reason": "dependency not ready"}
```

Complete a stage only after its phases are completed, cancelled, or superseded:

```json
{
  "op": "complete_stage",
  "id": "STG-002",
  "outcome": "Hierarchical ranking is complete",
  "evidence": ["all stage phases completed", "full ranking regression suite passes"],
  "lesson": "Validate graph traversal before tuning relevance weights.",
  "follow_on": "The recommendation stage can now consume stable ranking outputs."
}
```

Only one stage may be active. Use `complete_stage`, not a generic status change, for evidence-backed completion.

### Phases

Phases can be defined only under the active stage.

```json
{
  "op": "add_phase",
  "stage": "STG-002",
  "title": "Validate ancestry weighting",
  "intent": "Prove distance-based propagation through all supported depths.",
  "milestones": ["depth-one through depth-four tests pass"],
  "tags": ["ranking", "ancestry"],
  "vision": ["VIS-002"],
  "status": "active"
}
```

A planned phase exists in the roadmap data but gets a standalone phase file only when first activated.

```json
{"op": "set_phase_status", "id": "PH-007", "status": "paused", "reason": "auth became blocking", "resume_condition": "identity IDs are stable"}
```

```json
{
  "op": "complete_phase",
  "id": "PH-007",
  "outcome": "Ancestry weighting validated through four levels",
  "evidence": ["17 focused tests pass", "full regression suite passes"],
  "lesson": "Apply distance decay before popularity normalization.",
  "follow_on": "The caching phase can now use stable ranking outputs."
}
```

Completion requires nonempty evidence or `"unverified": true`.

```json
{"op": "cancel_phase", "id": "PH-009", "reason": "no longer contributes to the vision"}
{"op": "supersede_phase", "id": "PH-009", "by": "PH-014", "reason": "shared foundation replaces it"}
{"op": "move_phase", "id": "PH-003", "to_stage": "STG-004", "reason": "vote counters belong to the infra stage"}
```

A `by` reference must name an item that already exists; create the replacement earlier in the same delta or use `move_phase` when existing work belongs under a different stage. `move_phase` accepts any non-retired target stage, including a planned future one, and refuses to move an active phase.

### Values

- Stage: `planned`, `active`, `paused`, `completed`, `superseded`, `cancelled`
- Phase: `planned`, `active`, `paused`, `blocked`, `completed`, `superseded`, `cancelled`

## Narrative evidence

```json
{
  "accomplished": [
    {
      "what": "Corrected third-level ranking",
      "how": "Moved distance decay before normalization",
      "evidence": "17 focused tests pass",
      "phase": "PH-007",
      "files": ["src/ranking/ancestry.py"],
      "commits": ["a81f102"]
    }
  ],
  "failed": [
    {
      "attempt": "Normalize popularity before distance decay",
      "why": "Third-level weights exceeded second-level weights",
      "correction": "Apply distance decay first",
      "phase": "PH-007",
      "tags": ["ranking"]
    }
  ],
  "decisions": [
    {
      "what": "Use one messaging foundation for chat and DMs",
      "why": "Identity, notifications, and moderation overlap",
      "affects": ["VIS-011", "VIS-018"],
      "phase": "PH-012"
    }
  ],
  "lessons": [
    {
      "kind": "success",
      "summary": "Validate focused behavior before regression",
      "detail": "Run component tests before the full suite.",
      "tags": ["testing"],
      "related": ["PH-007"]
    }
  ]
}
```

Lesson kinds: `success`, `failure`, `correction`, `decision`, `preference`.

## Technical patch

Technical updates are also sparse:

```json
{
  "technical": {
    "summary": "Ranking passes through depth four.",
    "files": ["src/ranking/ancestry.py", "tests/test_ancestry.py"],
    "tests": ["17 focused tests pass", "full regression passes"],
    "environment": ["Python 3.12", "pytest"],
    "constraints": ["No unrestricted shell execution"],
    "known_issues": []
  }
}
```

Omitted technical fields are preserved.

## Pending notes

Resolve only notes actually reconciled:

```json
{"resolve_notes": ["NOTE-003", "NOTE-005"]}
```

`NOTE-004` remains pending.

## Routine example

```json
{
  "expected_revision": 12,
  "session_summary": "validated ancestry weighting",
  "handoff": {
    "last_session": [
      "Completed PH-007 by correcting multiplier order.",
      "All focused and regression tests pass."
    ],
    "resume_point": {
      "action": "Review the completed phase and decide whether to activate caching",
      "location": ".savestate/stages/STG-002-ranking/phases/PH-007-ancestry-weighting.md",
      "start_with": "python3 .claude/skills/save-state/scripts/state.py phase PH-007",
      "expected": "a complete evidence-backed phase record",
      "verify": "python3 .claude/skills/save-state/scripts/state.py doctor"
    }
  },
  "handoff_ops": [
    {"op": "complete_target", "id": "ACT-031", "result": "17 focused tests pass"},
    {"op": "complete_target", "id": "ACT-032", "result": "full regression passes"}
  ],
  "goal_ops": [
    {
      "op": "complete_phase",
      "id": "PH-007",
      "outcome": "Ancestry weighting validated",
      "evidence": ["17 focused tests pass", "full regression passes"],
      "lesson": "Distance decay must precede normalization."
    }
  ],
  "accomplished": [
    {
      "what": "Corrected multiplier ordering",
      "how": "Moved distance decay before popularity normalization",
      "evidence": "17 focused tests pass",
      "phase": "PH-007"
    }
  ]
}
```
