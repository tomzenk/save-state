from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import GENERATED_NOTICE
from .util import bullets, numbered, write_text, sha256_text, month_key


def _item_by_id(items: list[dict], item_id: str) -> dict | None:
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def _title(items: list[dict], item_id: str) -> str:
    item = _item_by_id(items, item_id)
    return str(item.get("title") if item else "")




def _latest(values: list[str]) -> str:
    cleaned = [str(value) for value in values if value]
    return max(cleaned) if cleaned else ""


def _frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            flattened = " ".join(str(value).split())
            escaped = flattened.replace('"', '\\"')
            rendered = f'"{escaped}"'
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines)


def render_resume(value: Any) -> str:
    if not value:
        return "Not yet set."
    if isinstance(value, str):
        return value
    action = str(value.get("action") or "Not yet set.")
    lines = [f"**Action:** {action}"]
    labels = (
        ("location", "Location"),
        ("start_with", "Start with"),
        ("expected", "Expected"),
        ("verify", "Verify"),
    )
    for key, label in labels:
        if value.get(key):
            lines.append(f"- **{label}:** `{value[key]}`" if key in {"location", "start_with", "verify"} else f"- **{label}:** {value[key]}")
    return "\n".join(lines)


def render_current(data: dict) -> str:
    meta = data["metadata"]
    current = data["current"]
    stage_id = current.get("active_stage") or ""
    phase_id = current.get("active_phase") or ""
    stage_title = _title(data["stages"]["items"], stage_id)
    phase_title = _title(data["phases"]["items"], phase_id)
    repository = current.get("repository") or {}
    targets = [item for item in current.get("session_targets", []) if item.get("status", "open") == "open"]

    path_lines: list[str] = []
    if stage_id:
        path_lines.append(f"- **Stage:** {stage_id} - {stage_title}")
    if phase_id:
        path_lines.append(f"- **Phase:** {phase_id} - {phase_title}")
    if not path_lines:
        path_lines.append("- No active stage or phase has been set.")

    target_lines = []
    for target in targets:
        target_lines.append(f"{target.get('id', '')} - {target.get('text', '')}".strip(" -"))
    blocked = [item for item in current.get("session_targets", []) if item.get("status") == "blocked"]
    blocked_lines = []
    for target in blocked:
        reason = target.get("reason") or "no reason recorded"
        blocked_lines.append(f"{target.get('id', '')} - {target.get('text', '')} (blocked: {reason})")

    recent_changes = []
    for change in current.get("recent_phase_changes", []):
        if isinstance(change, dict):
            recent_changes.append(
                f"{change.get('phase', '')} - {change.get('status', '')}: {change.get('summary', '')}".strip(" -:")
            )
        else:
            recent_changes.append(str(change))

    return (
        _frontmatter(
            {
                "save_state_version": 3,
                "cycle_id": meta.get("active_cycle_id", ""),
                "revision": meta.get("revision", 0),
                "updated_at": meta.get("last_saved_at", ""),
                "claude_session_id": meta.get("active_claude_session_id", ""),
            }
        )
        + f"\n\n{GENERATED_NOTICE}\n\n"
        + "# Current State\n\n"
        + "## Active project path\n\n"
        + "\n".join(path_lines)
        + "\n\n## Last session\n\n"
        + bullets(current.get("last_session", []), "- No session progress has been recorded yet.")
        + "\n\n## Immediate resume point\n\n"
        + render_resume(current.get("resume_point"))
        + "\n\n## Session targets\n\n"
        + numbered(target_lines, "1. No targets have been set.")
        + "\n\n## Blocked targets\n\n"
        + bullets(blocked_lines, "- None currently blocked.")
        + "\n\n## Relevant issue from recent work\n\n"
        + bullets(current.get("relevant_issues", []), "- None currently recorded.")
        + "\n\n## Open threads\n\n"
        + bullets(current.get("open_threads", []), "- None currently recorded.")
        + "\n\n## Recent phase changes\n\n"
        + bullets(recent_changes, "- None currently recorded.")
        + "\n\n## Technical summary\n\n"
        + (current.get("technical_summary") or "No technical summary has been recorded.")
        + "\n\n## Repository handoff\n\n"
        + f"- **Branch:** `{repository.get('branch') or '(none)'}`\n"
        + f"- **HEAD:** `{repository.get('head') or '(none)'}`\n"
        + f"- **Worktree:** {repository.get('worktree') or 'unknown'}\n"
        + "- **Changed files:**\n"
        + ("\n".join(f"  - `{path}`" for path in repository.get("changed_files", [])) or "  - None")
        + "\n\n## Next-session boot sequence\n\n"
        + numbered(current.get("boot_sequence", []), "1. Read this file and confirm the intended direction with the user.")
        + "\n\n## Retrieval pointers\n\n"
        + "- Vision and adjacent ideas: `.savestate/VISION.md` or `state.py vision`\n"
        + "- Stage roadmap and phase status: `.savestate/GOALS.md`\n"
        + "- Current implementation reality: `.savestate/TECHNICAL_STATE.md`\n"
        + "- Reusable successes, failures, and corrections: `.savestate/LESSONS.md`\n"
        + "- Targeted lookup: `state.py find`\n"
        + "- Frozen prior states: `.savestate/history/`\n"
    )


def render_vision(data: dict) -> str:
    items = data["vision"]["items"]
    meta = data["metadata"]
    vision_updated = _latest([entry.get("at", "") for item in items for entry in item.get("history", [])])
    lines = [
        _frontmatter({"save_state_version": 3, "updated_at": vision_updated}),
        "",
        GENERATED_NOTICE,
        "",
        "# Project Vision",
        "",
        "The vision is the detailed repository of what the completed project is intended to become. Maturity describes how developed an idea is, not how permanently important it is.",
        "",
        "## Vision index",
        "",
    ]
    for maturity in ("confirmed", "developing", "unrefined"):
        lines.append(f"### {maturity.title()}")
        filtered = [i for i in items if i.get("maturity") == maturity and i.get("disposition", "active") == "active"]
        if not filtered:
            lines.append("- None")
        for item in filtered:
            tags = ", ".join(item.get("tags", [])) or "none"
            lines.append(f"- **{item['id']} - {item.get('summary', '')}**  ")
            lines.append(f"  Tags: {tags}")
        lines.append("")

    lines.extend(["## Detailed vision", ""])
    for maturity in ("confirmed", "developing", "unrefined"):
        lines.extend([f"### {maturity.title()}", ""])
        filtered = [i for i in items if i.get("maturity") == maturity and i.get("disposition", "active") == "active"]
        if not filtered:
            lines.extend(["None.", ""])
        for item in filtered:
            lines.extend(render_vision_item(item))

    retired = [i for i in items if i.get("disposition", "active") != "active"]
    lines.extend(["## Parked, rejected, and superseded ideas", ""])
    if not retired:
        lines.extend(["None.", ""])
    else:
        for item in retired:
            lines.extend(render_vision_item(item))
    return "\n".join(lines).rstrip() + "\n"


def render_vision_item(item: dict) -> list[str]:
    lines = [f"#### {item['id']} - {item.get('summary', '')}", ""]
    lines.append(f"- **Maturity:** {item.get('maturity', 'unrefined')}")
    lines.append(f"- **Disposition:** {item.get('disposition', 'active')}")
    lines.append(f"- **Tags:** {', '.join(item.get('tags', [])) or 'None'}")
    lines.append(f"- **Related:** {', '.join(item.get('related', [])) or 'None'}")
    if item.get("user_words"):
        lines.extend(["", "**User's original wording**", "", f"> {item['user_words']}"])
    if item.get("detail"):
        lines.extend(["", "**Developed concept**", "", str(item["detail"])])
    if item.get("open_questions"):
        lines.extend(["", "**Open questions**", "", bullets(item["open_questions"])])
    if item.get("history"):
        lines.extend(["", "**History**", ""])
        for entry in item["history"]:
            lines.append(f"- {entry.get('at', '')}: {entry.get('text', '')}")
    lines.append("")
    return lines


def render_goals(data: dict) -> str:
    meta = data["metadata"]
    current = data["current"]
    stages = data["stages"]["items"]
    phases = data["phases"]["items"]
    active_stage = current.get("active_stage")
    goals_updated = _latest([item.get("updated_at", "") for item in [*stages, *phases]])
    lines = [
        _frontmatter({"save_state_version": 3, "updated_at": goals_updated}),
        "",
        GENERATED_NOTICE,
        "",
        "# Goals and Roadmap",
        "",
        "Future stages remain descriptive. Only the active stage is decomposed into short-term phases.",
        "",
        "## Long-term stages",
        "",
    ]
    if not stages:
        lines.extend(["No stages have been defined.", ""])
    for stage in stages:
        marker = " ← active" if stage.get("id") == active_stage else ""
        lines.extend([
            f"### {stage['id']} - {stage.get('title', '')}{marker}",
            "",
            f"- **Status:** {stage.get('status', 'planned')}",
            f"- **Intent:** {stage.get('intent') or 'Not yet defined.'}",
            f"- **Vision links:** {', '.join(stage.get('vision', [])) or 'None'}",
            "- **Exit criteria:**",
            *(f"  - {criterion}" for criterion in stage.get("criteria", [])),
        ])
        if not stage.get("criteria"):
            lines.append("  - Not yet defined.")
        if stage.get("id") == active_stage or stage.get("status") in {"completed", "paused"}:
            stage_phases = [p for p in phases if p.get("stage") == stage["id"]]
            lines.extend(["", "**Phases**"])
            if not stage_phases:
                lines.append("- None")
            for phase in stage_phases:
                lines.append(
                    f"- **{phase['id']} - {phase.get('title', '')}** [{phase.get('status', 'planned')}]"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_technical(data: dict) -> str:
    meta = data["metadata"]
    technical = data["technical"]
    repo = technical.get("repository") or data["current"].get("repository") or {}
    lines = [
        _frontmatter({"save_state_version": 3, "updated_at": technical.get("updated_at", "")}),
        "",
        GENERATED_NOTICE,
        "",
        "# Technical State",
        "",
        "## Current implementation summary",
        "",
        technical.get("summary") or "No technical summary has been recorded.",
        "",
        "## Architecture and working components",
        "",
        bullets(technical.get("architecture", [])),
        "",
        "## Relevant files",
        "",
        bullets([f"`{path}`" for path in technical.get("files", [])]),
        "",
        "## Tests and verification",
        "",
        bullets(technical.get("tests", [])),
        "",
        "## Environment",
        "",
        bullets(technical.get("environment", [])),
        "",
        "## Constraints",
        "",
        bullets(technical.get("constraints", [])),
        "",
        "## Known technical issues",
        "",
        bullets(technical.get("known_issues", [])),
        "",
        "## Repository snapshot",
        "",
        f"- **Branch:** `{repo.get('branch') or '(none)'}`",
        f"- **HEAD:** `{repo.get('head') or '(none)'}`",
        f"- **Worktree:** {repo.get('worktree') or 'unknown'}",
        "- **Changed files:**",
        *(f"  - `{path}`" for path in repo.get("changed_files", [])),
    ]
    if not repo.get("changed_files"):
        lines.append("  - None")
    return "\n".join(lines).rstrip() + "\n"


def render_lessons(data: dict) -> str:
    meta = data["metadata"]
    items = data["lessons"]["items"]
    lessons_updated = _latest([item.get("created_at", "") for item in items])
    lines = [
        _frontmatter({"save_state_version": 3, "updated_at": lessons_updated}),
        "",
        GENERATED_NOTICE,
        "",
        "# Reusable Lessons",
        "",
        "Successful approaches are preserved alongside failures and corrections.",
        "",
    ]
    for kind in ("success", "failure", "correction", "decision", "preference"):
        lines.extend([f"## {kind.title()}", ""])
        filtered = [item for item in items if item.get("kind") == kind]
        if not filtered:
            lines.extend(["None.", ""])
        for item in filtered:
            lines.extend([
                f"### {item['id']} - {item.get('summary', '')}",
                "",
                f"- **Tags:** {', '.join(item.get('tags', [])) or 'None'}",
                f"- **Related:** {', '.join(item.get('related', [])) or 'None'}",
                "",
                item.get("detail") or item.get("summary", ""),
                "",
            ])
    return "\n".join(lines).rstrip() + "\n"


def render_pending(data: dict) -> str:
    meta = data["metadata"]
    all_items = data["pending"]["items"]
    items = [i for i in all_items if i.get("status", "pending") == "pending"]
    pending_updated = _latest([item.get("resolved_at") or item.get("created_at", "") for item in all_items])
    lines = [
        _frontmatter({"save_state_version": 3, "updated_at": pending_updated}),
        "",
        GENERATED_NOTICE,
        "",
        "# Pending SaveState Notes",
        "",
        "These are exact lightweight captures awaiting semantic reconciliation.",
        "",
    ]
    if not items:
        lines.extend(["No pending notes.", ""])
    for item in items:
        lines.extend([
            f"## {item['id']} - {item.get('created_at', '')}",
            "",
            f"> {item.get('text', '')}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def render_stage(stage: dict, phases: list[dict]) -> str:
    stage_phases = [p for p in phases if p.get("stage") == stage["id"]]
    lines = [
        _frontmatter(
            {
                "id": stage["id"],
                "status": stage.get("status", "planned"),
                "updated_at": stage.get("updated_at", ""),
            }
        ),
        "",
        GENERATED_NOTICE,
        "",
        f"# {stage['id']} - {stage.get('title', '')}",
        "",
        "## Intended outcome",
        "",
        stage.get("intent") or "Not yet defined.",
        "",
        "## Vision links",
        "",
        bullets(stage.get("vision", [])),
        "",
        "## Exit criteria",
        "",
        bullets(stage.get("criteria", [])),
        "",
        "## Phases",
        "",
    ]
    if not stage_phases:
        lines.append("- No phases have been defined.")
    for phase in stage_phases:
        lines.append(f"- **{phase['id']} - {phase.get('title', '')}** [{phase.get('status', 'planned')}]")
    lines.extend(["", "## Completion record", ""])
    completion = stage.get("completion") or {}
    if not completion:
        lines.append("Not completed.")
    else:
        lines.extend([
            f"- **Outcome:** {completion.get('outcome') or 'Completed'}",
            "- **Evidence:**",
            *(f"  - {item}" for item in completion.get("evidence", [])),
            f"- **Reusable lesson:** {completion.get('lesson') or 'None recorded'}",
            f"- **Follow-on impact:** {completion.get('follow_on') or 'None recorded'}",
        ])
        if not completion.get("evidence"):
            lines.append("  - Not verified")
    if stage.get("history"):
        lines.extend(["", "## History", ""])
        for entry in stage["history"]:
            lines.append(f"- {entry.get('at', '')}: {entry.get('text', '')}")
    return "\n".join(lines).rstrip() + "\n"


def render_phase(phase: dict) -> str:
    lines = [
        _frontmatter(
            {
                "id": phase["id"],
                "stage": phase.get("stage", ""),
                "status": phase.get("status", "planned"),
                "updated_at": phase.get("updated_at", ""),
            }
        ),
        "",
        GENERATED_NOTICE,
        "",
        f"# {phase['id']} - {phase.get('title', '')}",
        "",
        "## Parent stage",
        "",
        phase.get("stage") or "Not set.",
        "",
        "## Intended outcome",
        "",
        phase.get("intent") or "Not yet defined.",
        "",
        "## Milestone criteria",
        "",
        bullets(phase.get("milestones", [])),
        "",
        "## Current status and resume condition",
        "",
        f"- **Status:** {phase.get('status', 'planned')}",
        f"- **Reason:** {phase.get('status_reason') or 'None'}",
        f"- **Resume condition:** {phase.get('resume_condition') or 'None'}",
        "",
        "## Accomplishments and successful approaches",
        "",
    ]
    accomplishments = phase.get("accomplishments", [])
    if not accomplishments:
        lines.append("None yet.")
    for item in accomplishments:
        lines.extend([
            f"### {item.get('event', '')} - {item.get('what', '')}",
            "",
            f"- **How:** {item.get('how') or 'Not recorded'}",
            f"- **Evidence:** {item.get('evidence') or 'Not verified'}",
            "",
        ])
    lines.extend(["## Failed attempts and corrections", ""])
    failures = phase.get("failures", [])
    if not failures:
        lines.append("None recorded.")
    for item in failures:
        lines.extend([
            f"### {item.get('event', '')} - {item.get('attempt', '')}",
            "",
            f"- **Why it failed or remained inconclusive:** {item.get('why') or 'Not recorded'}",
            f"- **Correction:** {item.get('correction') or 'Not yet known'}",
            "",
        ])
    lines.extend(["## Decisions", ""])
    decisions = phase.get("decisions", [])
    if not decisions:
        lines.append("None recorded.")
    for item in decisions:
        lines.append(f"- **{item.get('event', '')}:** {item.get('what', '')} - {item.get('why', '')}")
    reopened = bool(phase.get("completion")) and phase.get("status") != "completed"
    heading = "## Prior completion record (phase was reopened)" if reopened else "## Completion record"
    lines.extend(["", heading, ""])
    completion = phase.get("completion") or {}
    if not completion:
        lines.append("Not completed.")
    else:
        lines.extend([
            f"- **Outcome:** {completion.get('outcome') or 'Completed'}",
            "- **Evidence:**",
            *(f"  - {item}" for item in completion.get("evidence", [])),
            f"- **Reusable lesson:** {completion.get('lesson') or 'None recorded'}",
            f"- **Follow-on impact:** {completion.get('follow_on') or 'None recorded'}",
        ])
        if not completion.get("evidence"):
            lines.append("  - Not verified")
    lines.extend([
        "",
        "## Technical references",
        "",
        f"- **Files:** {', '.join(f'`{x}`' for x in phase.get('files', [])) or 'None'}",
        f"- **Commits:** {', '.join(phase.get('commits', [])) or 'None'}",
        f"- **Vision links:** {', '.join(phase.get('vision', [])) or 'None'}",
        f"- **Tags:** {', '.join(phase.get('tags', [])) or 'None'}",
        "",
        "## History",
        "",
    ])
    if not phase.get("history"):
        lines.append("No history recorded.")
    for entry in phase.get("history", []):
        lines.append(f"- {entry.get('at', '')}: {entry.get('text', '')}")
    return "\n".join(lines).rstrip() + "\n"


def build_index(data: dict) -> dict:
    index = {"by_id": {}, "by_tag": {}, "by_file": {}, "by_text": {}}

    def add_tag(tag: str, item_id: str) -> None:
        index["by_tag"].setdefault(tag.lower(), []).append(item_id)

    for item in data["vision"]["items"]:
        index["by_id"][item["id"]] = {"kind": "vision", "summary": item.get("summary", "")}
        for tag in item.get("tags", []):
            add_tag(tag, item["id"])
    for item in data["stages"]["items"]:
        index["by_id"][item["id"]] = {"kind": "stage", "summary": item.get("title", "")}
    for item in data["phases"]["items"]:
        index["by_id"][item["id"]] = {
            "kind": "phase",
            "summary": item.get("title", ""),
            "stage": item.get("stage", ""),
            "status": item.get("status", ""),
        }
        for tag in item.get("tags", []):
            add_tag(tag, item["id"])
        for path in item.get("files", []):
            index["by_file"].setdefault(path, []).append(item["id"])
    for item in data["lessons"]["items"]:
        index["by_id"][item["id"]] = {"kind": "lesson", "summary": item.get("summary", "")}
        for tag in item.get("tags", []):
            add_tag(tag, item["id"])
    for item in data["events"]["items"]:
        index["by_id"][item["id"]] = {"kind": "event", "summary": item.get("text", "")}
        for path in item.get("files", []):
            index["by_file"].setdefault(path, []).append(item["id"])
    for key in ("by_tag", "by_file"):
        for index_key, values in index[key].items():
            index[key][index_key] = sorted(set(values))
    return index


def render_index(data: dict) -> str:
    """A capped orientation card, not a table of contents. Full lookup lives
    in data/index.json and is queried through the find and vision commands;
    listing every ID here made this file the most expensive accidental read
    in the system, so it now shows counts and the live edge only."""
    index_updated = _latest([event.get("at", "") for event in data["events"]["items"]])
    recent_keep = 5

    def recent(items: list[dict], label_key: str) -> list[str]:
        tail = items[-recent_keep:]
        return [f"- **{item.get('id', '')}** - {str(item.get(label_key, ''))[:80]}" for item in reversed(tail)]

    counts = {
        "vision items": len(data["vision"]["items"]),
        "stages": len(data["stages"]["items"]),
        "phases": len(data["phases"]["items"]),
        "lessons": len(data["lessons"]["items"]),
        "events": len(data["events"]["items"]),
        "pending notes": len([i for i in data["pending"]["items"] if i.get("status", "pending") == "pending"]),
    }
    lines = [
        _frontmatter({"save_state_version": 3, "updated_at": index_updated}),
        "",
        GENERATED_NOTICE,
        "",
        "# SaveState Orientation",
        "",
        "Counts: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + ".",
        "",
        "Use targeted retrieval instead of reading state files whole:",
        "`state.py find --id/--tag/--file/--text`, `state.py vision --tags`, `state.py phase PH-###`.",
        "",
        "## Recent phases",
        "",
        *(recent(data["phases"]["items"], "title") or ["- None yet"]),
        "",
        "## Recent lessons",
        "",
        *(recent(data["lessons"]["items"], "summary") or ["- None yet"]),
        "",
        "## Recent vision items",
        "",
        *(recent(data["vision"]["items"], "summary") or ["- None yet"]),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_month_log(data: dict, month: str) -> str:
    events = [e for e in data["events"]["items"] if str(e.get("at", "")).startswith(month)]
    lines = [f"# SaveState Log - {month}", "", GENERATED_NOTICE, ""]
    if not events:
        lines.append("No events.")
    for event in events:
        lines.extend([
            f"## {event['id']} - {event.get('kind', '').title()}",
            "",
            f"- **Time:** {event.get('at', '')}",
            f"- **Cycle:** {event.get('cycle', '')}",
            f"- **Related:** {', '.join(event.get('related', [])) or 'None'}",
            "",
            event.get("text", ""),
            "",
        ])
        if event.get("detail"):
            lines.extend([event["detail"], ""])
    return "\n".join(lines).rstrip() + "\n"


def render_all(store, data: dict) -> dict[str, str]:
    data["index"] = build_index(data)
    outputs: dict[str, str] = {
        "CURRENT.md": render_current(data),
        "VISION.md": render_vision(data),
        "GOALS.md": render_goals(data),
        "TECHNICAL_STATE.md": render_technical(data),
        "LESSONS.md": render_lessons(data),
        "INDEX.md": render_index(data),
        "PENDING.md": render_pending(data),
    }

    for stage in data["stages"]["items"]:
        slug = stage.get("slug") or stage["id"].lower()
        path = f"stages/{stage['id']}-{slug}/STAGE.md"
        outputs[path] = render_stage(stage, data["phases"]["items"])
    for phase in data["phases"]["items"]:
        # Standalone phase files begin when a phase first leaves the planned
        # status. The activated_at marker remains after completion, pause, or
        # cancellation, and completion stamps it even for inline completions.
        if not phase.get("activated_at") and phase.get("status") == "planned":
            continue
        stage = _item_by_id(data["stages"]["items"], phase.get("stage", "")) or {}
        stage_slug = stage.get("slug") or phase.get("stage", "stage").lower()
        phase_slug = phase.get("slug") or phase["id"].lower()
        path = f"stages/{phase.get('stage')}-{stage_slug}/phases/{phase['id']}-{phase_slug}.md"
        outputs[path] = render_phase(phase)

    months = sorted({month_key(event.get("at")) for event in data["events"]["items"]})
    for month in months:
        outputs[f"logs/{month}.md"] = render_month_log(data, month)
    return outputs


def write_outputs(store, data: dict) -> dict[str, str]:
    outputs = render_all(store, data)
    expected_paths = {store.state_dir / rel for rel in outputs}
    for rel, text in outputs.items():
        write_text(store.state_dir / rel, text)

    # Remove generated stage/phase/log Markdown no longer represented by data.
    for base in (store.stages_dir, store.logs_dir):
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            if path not in expected_paths:
                path.unlink()
        for directory in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass

    hashes = {rel: sha256_text(text) for rel, text in outputs.items()}
    data["metadata"]["render_hashes"] = hashes
    return outputs
