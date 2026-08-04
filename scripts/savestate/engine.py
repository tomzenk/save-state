from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .constants import (
    ACTION_STATUSES,
    EVENT_KINDS,
    FORMAT_VERSION,
    LESSON_KINDS,
    PHASE_STATUSES,
    STAGE_STATUSES,
    VISION_DISPOSITIONS,
    VISION_MATURITIES,
)
from .gitutils import context as git_context
from .render import render_all, write_outputs
from .store import Store
from .util import (
    approx_tokens,
    compact_stamp,
    iso_now,
    sha256_text,
    slugify,
    unique,
    write_json,
)


class SaveStateError(RuntimeError):
    pass


def item_by_id(items: list[dict], item_id: str) -> dict | None:
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def alloc(meta: dict, prefix: str) -> str:
    counters = meta.setdefault("counters", {})
    counters[prefix] = int(counters.get(prefix, 0)) + 1
    width = max(3, len(str(counters[prefix])))
    return f"{prefix}-{counters[prefix]:0{width}d}"


def add_event(
    data: dict,
    kind: str,
    text: str,
    *,
    related: list[str] | None = None,
    detail: str = "",
    files: list[str] | None = None,
) -> str:
    if kind not in EVENT_KINDS:
        raise SaveStateError(f"Unsupported event kind: {kind}")
    event_id = alloc(data["metadata"], "EVT")
    data["events"]["items"].append(
        {
            "id": event_id,
            "kind": kind,
            "text": text,
            "detail": detail,
            "related": related or [],
            "files": files or [],
            "at": iso_now(),
            "cycle": data["metadata"].get("active_cycle_id", ""),
        }
    )
    return event_id


RECENT_CHANGE_KEEP = 5


def rotate_hot_fields(current: dict) -> None:
    """Boundary hygiene for the hot handoff. The full record of everything
    trimmed here already lives in the event log, phase histories, and the
    boundary snapshot taken moments earlier, so the hot file keeps recency,
    not archaeology."""
    changes = current.get("recent_phase_changes", [])
    if len(changes) > RECENT_CHANGE_KEEP:
        current["recent_phase_changes"] = changes[-RECENT_CHANGE_KEEP:]
    last = [str(x) for x in current.get("last_session", []) if str(x).strip()]
    if last:
        joined = " ".join(last)
        if len(joined) > 220:
            joined = joined[:217].rstrip() + "..."
        current["last_session"] = [f"Carried from previous session: {joined}"]


def open_cycle(data: dict, session_id: str, label: str = "") -> str:
    cycle_id = alloc(data["metadata"], "SSC")
    data["metadata"]["active_cycle_id"] = cycle_id
    data["metadata"]["active_claude_session_id"] = session_id
    data["metadata"]["cycle_started_at"] = iso_now()
    add_event(
        data,
        "cycle",
        f"Opened SaveState cycle {cycle_id}" + (f" ({label})" if label else ""),
        related=[cycle_id],
    )
    return cycle_id


def detect_boundary(data: dict, session_id: str, requested: str = "auto") -> str:
    if requested not in {"auto", "same", "new"}:
        raise SaveStateError("boundary must be auto, same, or new")
    if requested != "auto":
        return requested
    meta = data["metadata"]
    if not meta.get("active_cycle_id"):
        return "new"
    active_session = str(meta.get("active_claude_session_id") or "")
    if session_id and active_session and session_id != active_session:
        return "new"
    return "same"


def snapshot_bundle(store: Store, data: dict, reason: str, label: str = "") -> str:
    cycle_id = data["metadata"].get("active_cycle_id") or "unassigned"
    suffix = f"__{slugify(label)}" if label else ""
    name = f"{cycle_id}__{compact_stamp()}__{slugify(reason)}{suffix}"
    target = store.history_dir / name
    target.mkdir(parents=True, exist_ok=False)

    for filename in (
        "CURRENT.md",
        "VISION.md",
        "GOALS.md",
        "TECHNICAL_STATE.md",
        "LESSONS.md",
        "INDEX.md",
        "PENDING.md",
        "config.json",
        "metadata.json",
    ):
        source = store.state_dir / filename
        if source.exists():
            shutil.copy2(source, target / filename)

    include_events = bool(data["config"].get("snapshot", {}).get("include_events", False))
    data_target = target / "data"
    if store.data_dir.exists():
        ignore = None if include_events else shutil.ignore_patterns("events.json")
        shutil.copytree(store.data_dir, data_target, ignore=ignore)

    current = data["current"]
    stage_id = current.get("active_stage")
    phase_id = current.get("active_phase")
    copied: list[str] = []
    if store.stages_dir.exists():
        # Copy the active stage's whole directory: its STAGE.md plus every
        # phase file under it. That is the working set a recovery needs.
        working_dirs = {
            path.parent
            for path in store.stages_dir.glob("*/STAGE.md")
            if stage_id and path.parent.name.startswith(f"{stage_id}-")
        }
        # Also keep the active phase file even if it lives under another stage.
        extra_files = [
            path
            for path in store.stages_dir.rglob("*.md")
            if phase_id and path.name.startswith(f"{phase_id}-")
        ]
        for directory in working_dirs:
            for path in directory.rglob("*.md"):
                relative = path.relative_to(store.state_dir)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                copied.append(str(relative))
        for path in extra_files:
            relative = path.relative_to(store.state_dir)
            destination = target / relative
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                copied.append(str(relative))

    manifest = {
        "save_state_version": FORMAT_VERSION,
        "cycle_id": cycle_id,
        "created_at": iso_now(),
        "reason": reason,
        "label": label,
        "active_stage": stage_id,
        "active_phase": phase_id,
        "repository": git_context(store.project_root),
        "copied_working_records": sorted(set(copied)),
        "events_included": include_events,
        "revision": data["metadata"].get("revision", 0),
    }
    write_json(target / "manifest.json", manifest)
    data["metadata"]["last_snapshot"] = str(target.relative_to(store.state_dir))
    return str(target.relative_to(store.state_dir))


TARGET_FIELDS = ("id", "text", "status", "result", "reason")


def normalize_target(data: dict, value: Any) -> dict:
    if isinstance(value, str):
        return {"id": alloc(data["metadata"], "ACT"), "text": value, "status": "open"}
    if not isinstance(value, dict):
        raise SaveStateError("session target must be a string or object")
    target = {key: value[key] for key in TARGET_FIELDS if key in value}
    target.setdefault("id", alloc(data["metadata"], "ACT"))
    target.setdefault("status", "open")
    if target["status"] not in ACTION_STATUSES:
        raise SaveStateError(f"Invalid action status: {target['status']}")
    if not target.get("text"):
        raise SaveStateError("session target requires text")
    return target


def merge_handoff(data: dict, patch: dict) -> None:
    current = data["current"]
    scalar_fields = {
        "active_stage",
        "active_phase",
        "resume_point",
        "technical_summary",
    }
    list_fields = {
        "last_session",
        "relevant_issues",
        "open_threads",
        "boot_sequence",
        "recent_phase_changes",
    }
    allowed = scalar_fields | list_fields | {"session_targets"}
    unknown = set(patch) - allowed
    if unknown:
        raise SaveStateError(f"Unknown handoff fields: {', '.join(sorted(unknown))}")
    for key in scalar_fields:
        if key in patch:
            current[key] = patch[key]
    for key in list_fields:
        if key in patch:
            if not isinstance(patch[key], list):
                raise SaveStateError(f"handoff.{key} must be a list")
            current[key] = patch[key]
    if "session_targets" in patch:
        if not isinstance(patch["session_targets"], list):
            raise SaveStateError("handoff.session_targets must be a list")
        current["session_targets"] = [normalize_target(data, item) for item in patch["session_targets"]]


def apply_handoff_ops(data: dict, operations: list[dict]) -> None:
    current = data["current"]
    for op in operations:
        kind = op.get("op")
        if kind == "append_last_session":
            text = str(op.get("text") or "").strip()
            if text:
                current["last_session"].append(text)
        elif kind == "add_target":
            current["session_targets"].append(normalize_target(data, op))
        elif kind in {"complete_target", "block_target", "cancel_target", "supersede_target"}:
            target = item_by_id(current["session_targets"], str(op.get("id") or ""))
            if not target:
                raise SaveStateError(f"Unknown action target: {op.get('id')}")
            status_map = {
                "complete_target": "completed",
                "block_target": "blocked",
                "cancel_target": "cancelled",
                "supersede_target": "superseded",
            }
            target["status"] = status_map[kind]
            if op.get("result"):
                target["result"] = op["result"]
            if op.get("reason"):
                target["reason"] = op["reason"]
        elif kind == "carry_forward_target":
            target = item_by_id(current["session_targets"], str(op.get("id") or ""))
            if not target:
                raise SaveStateError(f"Unknown action target: {op.get('id')}")
            target["status"] = "open"
            if op.get("text"):
                target["text"] = op["text"]
        elif kind == "set_resume_point":
            value = op.get("value")
            if value is None:
                value = {key: op.get(key, "") for key in ("action", "location", "start_with", "expected", "verify")}
            current["resume_point"] = value
        elif kind == "set_active_path":
            if "stage" in op:
                current["active_stage"] = op.get("stage") or ""
            if "phase" in op:
                current["active_phase"] = op.get("phase") or ""
        elif kind in {"add_issue", "add_thread"}:
            key = "relevant_issues" if kind == "add_issue" else "open_threads"
            text = str(op.get("text") or "").strip()
            if text and text not in current[key]:
                current[key].append(text)
        elif kind in {"resolve_issue", "resolve_thread"}:
            key = "relevant_issues" if kind == "resolve_issue" else "open_threads"
            text = str(op.get("text") or "")
            match = text if text in current[key] else None
            if match is None:
                # Fall back to a whitespace-normalized comparison so an entry
                # containing newlines can be resolved from its rendered form,
                # but only when exactly one entry matches.
                normalized = " ".join(text.split())
                candidates = [item for item in current[key] if " ".join(str(item).split()) == normalized]
                if len(candidates) == 1:
                    match = candidates[0]
                elif len(candidates) > 1:
                    raise SaveStateError(f"{kind} text matches several entries; supply the exact one")
            if match is None:
                existing = "; ".join(f'"{item}"' for item in current[key]) or "(none)"
                raise SaveStateError(
                    f"{kind} requires the existing text. "
                    f'No entry matches "{text}". Current entries: {existing}'
                )
            current[key] = [item for item in current[key] if item != match]
        elif kind == "clear_recent_phase_changes":
            current["recent_phase_changes"] = []
        else:
            raise SaveStateError(f"Unsupported handoff operation: {kind}")


def apply_vision_ops(data: dict, operations: list[dict]) -> None:
    items = data["vision"]["items"]
    for op in operations:
        kind = op.get("op")
        if kind == "add":
            maturity = op.get("maturity", "unrefined")
            if maturity not in VISION_MATURITIES:
                raise SaveStateError(f"Invalid vision maturity: {maturity}")
            if maturity == "confirmed" and not op.get("user_approved"):
                raise SaveStateError("Adding a confirmed vision item requires user_approved=true")
            item_id = alloc(data["metadata"], "VIS")
            item = {
                "id": item_id,
                "summary": str(op.get("summary") or "").strip(),
                "maturity": maturity,
                "disposition": op.get("disposition", "active"),
                "tags": unique([str(x) for x in op.get("tags", [])]),
                "related": unique([str(x) for x in op.get("related", [])]),
                "detail": op.get("detail", ""),
                "user_words": op.get("user_words", ""),
                "open_questions": op.get("open_questions", []),
                "history": [{"at": iso_now(), "text": f"Added as {maturity}."}],
            }
            if not item["summary"]:
                raise SaveStateError("Vision item requires summary")
            if item["disposition"] not in VISION_DISPOSITIONS:
                raise SaveStateError(f"Invalid vision disposition: {item['disposition']}")
            items.append(item)
            add_event(data, "plan", f"Added vision item {item_id}: {item['summary']}", related=[item_id])
            continue

        item = item_by_id(items, str(op.get("id") or ""))
        if not item:
            raise SaveStateError(f"Unknown vision item: {op.get('id')}")
        was_confirmed = item.get("maturity") == "confirmed" and item.get("disposition", "active") == "active"

        if kind == "update":
            meaning_fields = {"summary", "detail", "maturity", "disposition"}
            if was_confirmed and meaning_fields.intersection(op) and not op.get("user_approved"):
                raise SaveStateError(
                    f"Meaningful changes to confirmed item {item['id']} require user_approved=true"
                )
            for key in ("summary", "detail", "user_words", "open_questions"):
                if key in op:
                    item[key] = op[key]
            for key in ("tags", "related"):
                if key in op:
                    item[key] = unique([str(x) for x in op[key]])
            item["history"].append({"at": iso_now(), "text": op.get("reason") or "Updated."})
            add_event(data, "decision", f"Updated vision item {item['id']}", related=[item["id"]])
        elif kind in {"promote", "demote"}:
            target = op.get("to")
            if target not in VISION_MATURITIES:
                raise SaveStateError(f"Invalid vision maturity: {target}")
            if (was_confirmed or target == "confirmed") and not op.get("user_approved"):
                raise SaveStateError("Changing confirmed vision maturity requires user_approved=true")
            reason = str(op.get("reason") or "").strip()
            if not reason:
                raise SaveStateError("Vision maturity change requires reason")
            item["maturity"] = target
            item["history"].append({"at": iso_now(), "text": f"Changed maturity to {target}: {reason}"})
            add_event(data, "decision", f"Changed {item['id']} maturity to {target}: {reason}", related=[item["id"]])
        elif kind in {"park", "reject", "supersede"}:
            if was_confirmed and not op.get("user_approved"):
                raise SaveStateError("Retiring a confirmed vision item requires user_approved=true")
            reason = str(op.get("reason") or "").strip()
            if not reason:
                raise SaveStateError("Vision disposition change requires reason")
            disposition = {"park": "parked", "reject": "rejected", "supersede": "superseded"}[kind]
            item["disposition"] = disposition
            if op.get("by"):
                if not item_by_id(items, str(op["by"])):
                    raise SaveStateError(
                        f"supersede by={op['by']} references a vision item that does not exist. "
                        "Create the replacement first (earlier in the same delta is fine)."
                    )
                item["superseded_by"] = op["by"]
            item["history"].append({"at": iso_now(), "text": f"{disposition.title()}: {reason}"})
            add_event(data, "removal", f"{item['id']} {disposition}: {reason}", related=[item["id"], *([op["by"]] if op.get("by") else [])])
        elif kind == "reactivate":
            if not op.get("user_approved"):
                raise SaveStateError("Reactivating a retired idea requires user_approved=true")
            reason = str(op.get("reason") or "").strip()
            if not reason:
                raise SaveStateError("Reactivation requires reason")
            item["disposition"] = "active"
            item["history"].append({"at": iso_now(), "text": f"Reactivated: {reason}"})
            add_event(data, "reopen", f"Reactivated {item['id']}: {reason}", related=[item["id"]])
        else:
            raise SaveStateError(f"Unsupported vision operation: {kind}")


def _active_stage(data: dict) -> dict | None:
    return item_by_id(data["stages"]["items"], data["current"].get("active_stage", ""))


def _active_phase(data: dict) -> dict | None:
    return item_by_id(data["phases"]["items"], data["current"].get("active_phase", ""))


def apply_goal_ops(data: dict, operations: list[dict]) -> None:
    stages = data["stages"]["items"]
    phases = data["phases"]["items"]
    current = data["current"]

    for op in operations:
        kind = op.get("op")
        if kind == "add_stage":
            stage_id = alloc(data["metadata"], "STG")
            status = op.get("status", "planned")
            if status not in STAGE_STATUSES:
                raise SaveStateError(f"Invalid stage status: {status}")
            if status == "active" and any(s.get("status") == "active" for s in stages):
                raise SaveStateError("Pause or complete the current active stage before activating another")
            stage = {
                "id": stage_id,
                "title": str(op.get("title") or "").strip(),
                "slug": slugify(str(op.get("title") or stage_id).lower()),
                "intent": op.get("intent", ""),
                "criteria": op.get("criteria", []),
                "vision": op.get("vision", []),
                "status": status,
                "created_at": iso_now(),
                "updated_at": iso_now(),
                "completion": {},
                "history": [{"at": iso_now(), "text": f"Created with status {status}."}],
            }
            if not stage["title"]:
                raise SaveStateError("Stage requires title")
            stages.append(stage)
            if status == "active":
                current["active_stage"] = stage_id
                current["active_phase"] = ""
            add_event(data, "plan", f"Added stage {stage_id}: {stage['title']}", related=[stage_id, *stage["vision"]])
        elif kind == "set_stage_status":
            stage = item_by_id(stages, str(op.get("id") or ""))
            if not stage:
                raise SaveStateError(f"Unknown stage: {op.get('id')}")
            status = op.get("status")
            if status not in STAGE_STATUSES:
                raise SaveStateError(f"Invalid stage status: {status}")
            if status == "completed":
                raise SaveStateError("Use complete_stage so evidence and unfinished phases are validated")
            if status == "active":
                other = [s["id"] for s in stages if s.get("status") == "active" and s["id"] != stage["id"]]
                if other:
                    raise SaveStateError(f"Another stage is active: {', '.join(other)}")
                current["active_stage"] = stage["id"]
            elif current.get("active_stage") == stage["id"] and status != "active":
                current["active_stage"] = ""
                current["active_phase"] = ""
            stage["status"] = status
            stage["updated_at"] = iso_now()
            reason = op.get("reason") or f"Status changed to {status}."
            stage["history"].append({"at": iso_now(), "text": reason})
            add_event(data, "plan", f"Stage {stage['id']} changed to {status}: {reason}", related=[stage["id"]])
        elif kind == "complete_stage":
            stage = item_by_id(stages, str(op.get("id") or ""))
            if not stage:
                raise SaveStateError(f"Unknown stage: {op.get('id')}")
            unfinished = [
                phase["id"]
                for phase in phases
                if phase.get("stage") == stage["id"]
                and phase.get("status") not in {"completed", "cancelled", "superseded"}
            ]
            if unfinished:
                raise SaveStateError(
                    f"Cannot complete {stage['id']} while phases remain unfinished: {', '.join(unfinished)}"
                )
            evidence = [str(x) for x in op.get("evidence", []) if str(x).strip()]
            if not evidence and not op.get("unverified"):
                raise SaveStateError("Completing a stage requires evidence or unverified=true")
            stage["status"] = "completed"
            stage["updated_at"] = iso_now()
            stage["completion"] = {
                "outcome": op.get("outcome", "Completed"),
                "evidence": evidence,
                "lesson": op.get("lesson", ""),
                "follow_on": op.get("follow_on", ""),
                "completed_at": iso_now(),
                "unverified": bool(op.get("unverified")),
            }
            stage["history"].append({"at": iso_now(), "text": f"Completed: {stage['completion']['outcome']}"})
            if current.get("active_stage") == stage["id"]:
                current["active_stage"] = ""
                current["active_phase"] = ""
            event_id = add_event(
                data,
                "completion",
                f"Completed {stage['id']}: {stage['completion']['outcome']}",
                related=[stage["id"], *stage.get("vision", [])],
                detail="; ".join(evidence),
            )
            if op.get("lesson"):
                add_lesson(
                    data,
                    {
                        "kind": "success",
                        "summary": f"How {stage['id']} succeeded",
                        "detail": op["lesson"],
                        "related": [stage["id"], event_id],
                    },
                )
        elif kind == "add_phase":
            stage = item_by_id(stages, str(op.get("stage") or ""))
            if not stage:
                raise SaveStateError(f"Unknown parent stage: {op.get('stage')}")
            if stage.get("status") != "active":
                raise SaveStateError(
                    f"Phases may only be defined for the active stage; {stage['id']} is {stage.get('status')}"
                )
            status = op.get("status", "planned")
            if status not in PHASE_STATUSES:
                raise SaveStateError(f"Invalid phase status: {status}")
            if status == "active" and any(p.get("status") == "active" for p in phases):
                raise SaveStateError("Pause, block, or complete the current active phase before activating another")
            phase_id = alloc(data["metadata"], "PH")
            phase = {
                "id": phase_id,
                "stage": stage["id"],
                "title": str(op.get("title") or "").strip(),
                "slug": slugify(str(op.get("title") or phase_id).lower()),
                "intent": op.get("intent", ""),
                "milestones": op.get("milestones", []),
                "vision": op.get("vision", []),
                "tags": op.get("tags", []),
                "status": status,
                "status_reason": op.get("reason", ""),
                "resume_condition": op.get("resume_condition", ""),
                "created_at": iso_now(),
                "updated_at": iso_now(),
                "activated_at": iso_now() if status == "active" else "",
                "accomplishments": [],
                "failures": [],
                "decisions": [],
                "files": [],
                "commits": [],
                "completion": {},
                "history": [{"at": iso_now(), "text": f"Created with status {status}."}],
            }
            if not phase["title"]:
                raise SaveStateError("Phase requires title")
            phases.append(phase)
            if status == "active":
                current["active_stage"] = stage["id"]
                current["active_phase"] = phase_id
            add_event(data, "plan", f"Added phase {phase_id}: {phase['title']}", related=[phase_id, stage["id"], *phase["vision"]])
        elif kind == "set_phase_status":
            phase = item_by_id(phases, str(op.get("id") or ""))
            if not phase:
                raise SaveStateError(f"Unknown phase: {op.get('id')}")
            status = op.get("status")
            if status not in PHASE_STATUSES:
                raise SaveStateError(f"Invalid phase status: {status}")
            if status == "active":
                parent = item_by_id(stages, phase["stage"])
                if not parent or parent.get("status") != "active":
                    raise SaveStateError("A phase can become active only under the active stage")
                other = [p["id"] for p in phases if p.get("status") == "active" and p["id"] != phase["id"]]
                if other:
                    raise SaveStateError(f"Another phase is active: {', '.join(other)}")
                phase["activated_at"] = phase.get("activated_at") or iso_now()
                current["active_stage"] = phase["stage"]
                current["active_phase"] = phase["id"]
            elif current.get("active_phase") == phase["id"] and status != "active":
                current["active_phase"] = ""
            phase["status"] = status
            phase["status_reason"] = op.get("reason", "")
            phase["resume_condition"] = op.get("resume_condition", phase.get("resume_condition", ""))
            phase["updated_at"] = iso_now()
            phase["history"].append({"at": iso_now(), "text": op.get("reason") or f"Status changed to {status}."})
            current["recent_phase_changes"].append(
                {"phase": phase["id"], "status": status, "summary": op.get("reason") or phase.get("title", "")}
            )
            add_event(data, "plan", f"Phase {phase['id']} changed to {status}: {op.get('reason', '')}", related=[phase["id"], phase["stage"]])
        elif kind == "complete_phase":
            phase = item_by_id(phases, str(op.get("id") or ""))
            if not phase:
                raise SaveStateError(f"Unknown phase: {op.get('id')}")
            evidence = [str(x) for x in op.get("evidence", []) if str(x).strip()]
            if not evidence and not op.get("unverified"):
                raise SaveStateError("Completing a phase requires evidence or unverified=true")
            phase["status"] = "completed"
            # A phase completed without ever being activated still deserves a
            # durable standalone record; stamp activation at completion time.
            phase["activated_at"] = phase.get("activated_at") or iso_now()
            phase["updated_at"] = iso_now()
            phase["completion"] = {
                "outcome": op.get("outcome", "Completed"),
                "evidence": evidence,
                "lesson": op.get("lesson", ""),
                "follow_on": op.get("follow_on", ""),
                "completed_at": iso_now(),
                "unverified": bool(op.get("unverified")),
            }
            phase["history"].append({"at": iso_now(), "text": f"Completed: {phase['completion']['outcome']}"})
            if current.get("active_phase") == phase["id"]:
                current["active_phase"] = ""
            current["recent_phase_changes"].append(
                {"phase": phase["id"], "status": "completed", "summary": phase["completion"]["outcome"]}
            )
            event_id = add_event(
                data,
                "completion",
                f"Completed {phase['id']}: {phase['completion']['outcome']}",
                related=[phase["id"], phase["stage"]],
                detail="; ".join(evidence),
                files=phase.get("files", []),
            )
            if op.get("lesson"):
                add_lesson(
                    data,
                    {
                        "kind": "success",
                        "summary": f"How {phase['id']} succeeded",
                        "detail": op["lesson"],
                        "tags": phase.get("tags", []),
                        "related": [phase["id"], event_id],
                    },
                )
        elif kind == "move_phase":
            phase = item_by_id(phases, str(op.get("id") or ""))
            if not phase:
                raise SaveStateError(f"Unknown phase: {op.get('id')}")
            target = item_by_id(stages, str(op.get("to_stage") or ""))
            if not target:
                raise SaveStateError(f"move_phase to_stage {op.get('to_stage')} does not exist")
            if phase.get("status") == "active":
                raise SaveStateError(
                    "Pause or complete the phase before moving it; an active phase must stay under the active stage"
                )
            if target.get("status") in {"completed", "cancelled", "superseded"}:
                raise SaveStateError(f"Cannot move a phase into retired stage {target['id']}")
            reason = str(op.get("reason") or "").strip()
            if not reason:
                raise SaveStateError("move_phase requires reason")
            old_stage = phase.get("stage", "")
            phase["stage"] = target["id"]
            phase["updated_at"] = iso_now()
            phase["history"].append({"at": iso_now(), "text": f"Moved from {old_stage} to {target['id']}: {reason}"})
            add_event(data, "plan", f"Moved {phase['id']} from {old_stage} to {target['id']}: {reason}", related=[phase["id"], old_stage, target["id"]])
        elif kind in {"cancel_phase", "supersede_phase"}:
            phase = item_by_id(phases, str(op.get("id") or ""))
            if not phase:
                raise SaveStateError(f"Unknown phase: {op.get('id')}")
            reason = str(op.get("reason") or "").strip()
            if not reason:
                raise SaveStateError("Retiring a phase requires reason")
            status = "cancelled" if kind == "cancel_phase" else "superseded"
            phase["status"] = status
            phase["status_reason"] = reason
            if op.get("by"):
                if not item_by_id(phases, str(op["by"])):
                    raise SaveStateError(
                        f"supersede by={op['by']} references a phase that does not exist. "
                        "Create the replacement first (earlier in the same delta is fine), "
                        "or use move_phase if the work is moving to another stage."
                    )
                phase["superseded_by"] = op["by"]
            phase["updated_at"] = iso_now()
            phase["history"].append({"at": iso_now(), "text": f"{status.title()}: {reason}"})
            if current.get("active_phase") == phase["id"]:
                current["active_phase"] = ""
            current["recent_phase_changes"].append({"phase": phase["id"], "status": status, "summary": reason})
            add_event(data, "removal", f"{phase['id']} {status}: {reason}", related=[phase["id"], phase["stage"]])
        else:
            raise SaveStateError(f"Unsupported goal operation: {kind}")


def add_lesson(data: dict, raw: dict) -> str:
    kind = raw.get("kind", "correction")
    if kind not in LESSON_KINDS:
        raise SaveStateError(f"Invalid lesson kind: {kind}")
    lesson_id = alloc(data["metadata"], "LES")
    summary = str(raw.get("summary") or raw.get("text") or "").strip()
    if not summary:
        raise SaveStateError("Lesson requires summary or text")
    data["lessons"]["items"].append(
        {
            "id": lesson_id,
            "kind": kind,
            "summary": summary,
            "detail": raw.get("detail") or raw.get("text") or summary,
            "tags": raw.get("tags", []),
            "related": raw.get("related", []),
            "created_at": iso_now(),
        }
    )
    add_event(data, "correction" if kind in {"failure", "correction"} else "decision", f"Recorded lesson {lesson_id}: {summary}", related=[lesson_id, *raw.get("related", [])])
    return lesson_id


def apply_narrative(data: dict, delta: dict) -> list[str]:
    warnings: list[str] = []
    active = _active_phase(data)

    for item in delta.get("accomplished", []):
        phase_id = item.get("phase") or (active.get("id") if active else "")
        phase = item_by_id(data["phases"]["items"], phase_id) if phase_id else None
        evidence = str(item.get("evidence") or "").strip()
        if not evidence:
            warnings.append(f"Accomplishment lacks evidence: {item.get('what', '')}")
        event_id = add_event(
            data,
            "completion",
            str(item.get("what") or "Accomplishment"),
            related=[x for x in [phase_id] if x],
            detail=f"How: {item.get('how', '')}\nEvidence: {evidence or 'unverified'}",
            files=item.get("files", []),
        )
        if phase:
            phase["accomplishments"].append(
                {
                    "event": event_id,
                    "what": item.get("what", ""),
                    "how": item.get("how", ""),
                    "evidence": evidence,
                }
            )
            phase["files"] = unique([*phase.get("files", []), *item.get("files", [])])
            phase["commits"] = unique([*phase.get("commits", []), *item.get("commits", [])])
            phase["updated_at"] = iso_now()

    for item in delta.get("failed", []):
        phase_id = item.get("phase") or (active.get("id") if active else "")
        phase = item_by_id(data["phases"]["items"], phase_id) if phase_id else None
        event_id = add_event(
            data,
            "failure",
            str(item.get("attempt") or "Failed attempt"),
            related=[x for x in [phase_id] if x],
            detail=f"Why: {item.get('why', '')}\nCorrection: {item.get('correction', '')}",
            files=item.get("files", []),
        )
        if phase:
            phase["failures"].append(
                {
                    "event": event_id,
                    "attempt": item.get("attempt", ""),
                    "why": item.get("why", ""),
                    "correction": item.get("correction", ""),
                }
            )
            phase["files"] = unique([*phase.get("files", []), *item.get("files", [])])
            phase["updated_at"] = iso_now()
        if item.get("correction") and item.get("create_lesson", True):
            add_lesson(
                data,
                {
                    "kind": "correction",
                    "summary": str(item.get("lesson_summary") or item.get("attempt") or "Corrected approach"),
                    "detail": item.get("correction", ""),
                    "tags": item.get("tags", []),
                    "related": [x for x in [phase_id, event_id] if x],
                },
            )

    for item in delta.get("decisions", []):
        phase_id = item.get("phase") or (active.get("id") if active else "")
        phase = item_by_id(data["phases"]["items"], phase_id) if phase_id else None
        related = unique([*item.get("affects", []), *([phase_id] if phase_id else [])])
        event_id = add_event(
            data,
            "decision",
            str(item.get("what") or "Decision"),
            related=related,
            detail=str(item.get("why") or ""),
        )
        if phase:
            phase["decisions"].append(
                {
                    "event": event_id,
                    "what": item.get("what", ""),
                    "why": item.get("why", ""),
                }
            )
            phase["updated_at"] = iso_now()

    for item in delta.get("lessons", []):
        add_lesson(data, item)
    return warnings


def merge_technical(data: dict, patch: dict) -> None:
    allowed = {
        "summary",
        "architecture",
        "files",
        "tests",
        "environment",
        "constraints",
        "known_issues",
    }
    unknown = set(patch) - allowed
    if unknown:
        raise SaveStateError(f"Unknown technical fields: {', '.join(sorted(unknown))}")
    changed = False
    for key, value in patch.items():
        if data["technical"].get(key) != value:
            data["technical"][key] = value
            changed = True
    if changed:
        data["technical"]["updated_at"] = iso_now()


def resolve_notes(data: dict, ids: list[str]) -> None:
    known = {item["id"]: item for item in data["pending"]["items"]}
    for note_id in ids:
        if note_id not in known:
            raise SaveStateError(f"Unknown pending note: {note_id}")
        known[note_id]["status"] = "resolved"
        known[note_id]["resolved_at"] = iso_now()


def apply_delta(data: dict, delta: dict, session_id: str) -> list[str]:
    warnings: list[str] = []
    meta = data["metadata"]
    boundary = detect_boundary(data, session_id, delta.get("boundary", "auto"))

    # Boundary archiving is performed by the caller before this function because
    # it must snapshot the pre-update rendered bundle.
    if boundary == "new":
        open_cycle(data, session_id, str(delta.get("cycle_label") or ""))
        rotate_hot_fields(data["current"])
    elif session_id and not meta.get("active_claude_session_id"):
        meta["active_claude_session_id"] = session_id

    if "handoff" in delta:
        if not isinstance(delta["handoff"], dict):
            raise SaveStateError("handoff must be an object")
        merge_handoff(data, delta["handoff"])
    apply_handoff_ops(data, delta.get("handoff_ops", []))
    apply_vision_ops(data, delta.get("vision_ops", []))
    apply_goal_ops(data, delta.get("goal_ops", []))
    warnings.extend(apply_narrative(data, delta))
    if "technical" in delta:
        merge_technical(data, delta["technical"])
    if delta.get("resolve_notes"):
        resolve_notes(data, [str(x) for x in delta["resolve_notes"]])

    return warnings


def update_repository_context(store: Store, data: dict) -> None:
    repo = git_context(store.project_root)
    current_repo = {
        "branch": repo.get("branch", ""),
        "head": repo.get("head", ""),
        "worktree": repo.get("worktree", "unknown"),
        "changed_files": repo.get("changed_files", []),
    }
    data["current"]["repository"] = current_repo
    if data["technical"].get("repository") != current_repo:
        data["technical"]["repository"] = current_repo
        data["technical"]["updated_at"] = iso_now()


def validate_data(data: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    meta = data["metadata"]
    if meta.get("format_version") != FORMAT_VERSION:
        errors.append(f"metadata format_version must be {FORMAT_VERSION}")

    all_items: list[dict] = []
    for key in ("vision", "stages", "phases", "lessons", "events", "pending"):
        all_items.extend(data[key].get("items", []))
    all_items.extend(data["current"].get("session_targets", []))
    ids = [str(item.get("id")) for item in all_items if item.get("id")]
    duplicates = sorted(item_id for item_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        errors.append(f"Duplicate IDs: {', '.join(duplicates)}")

    known_ids = set(ids)
    stages = data["stages"]["items"]
    phases = data["phases"]["items"]
    stage_ids = {item["id"] for item in stages}
    phase_ids = {item["id"] for item in phases}

    active_stages = [s["id"] for s in stages if s.get("status") == "active"]
    active_phases = [p["id"] for p in phases if p.get("status") == "active"]
    if len(active_stages) > 1:
        errors.append(f"Multiple active stages: {', '.join(active_stages)}")
    if len(active_phases) > 1:
        errors.append(f"Multiple active phases: {', '.join(active_phases)}")

    vision_ids = {item["id"] for item in data["vision"]["items"]}
    for stage in stages:
        if stage.get("status") not in STAGE_STATUSES:
            errors.append(f"{stage['id']} has invalid status {stage.get('status')}")
        for vision_id in stage.get("vision", []):
            if vision_id not in vision_ids:
                errors.append(f"{stage['id']} references unknown vision item {vision_id}")
        if stage.get("status") == "completed":
            completion = stage.get("completion") or {}
            if not completion.get("evidence") and not completion.get("unverified"):
                errors.append(f"Completed stage {stage['id']} has no evidence")
            unfinished = [p["id"] for p in phases if p.get("stage") == stage["id"] and p.get("status") not in {"completed", "cancelled", "superseded"}]
            if unfinished:
                errors.append(f"Completed stage {stage['id']} has unfinished phases: {', '.join(unfinished)}")
    for phase in phases:
        if phase.get("status") not in PHASE_STATUSES:
            errors.append(f"{phase['id']} has invalid status {phase.get('status')}")
        if phase.get("stage") not in stage_ids:
            errors.append(f"{phase['id']} references unknown stage {phase.get('stage')}")
        for vision_id in phase.get("vision", []):
            if vision_id not in vision_ids:
                errors.append(f"{phase['id']} references unknown vision item {vision_id}")
        if phase.get("status") == "active":
            parent = item_by_id(stages, phase.get("stage", ""))
            if not parent or parent.get("status") != "active":
                errors.append(f"Active phase {phase['id']} is not under the active stage")
        if phase.get("status") == "completed":
            completion = phase.get("completion") or {}
            if not completion.get("evidence") and not completion.get("unverified"):
                errors.append(f"Completed phase {phase['id']} has no evidence")

    current = data["current"]
    if current.get("active_stage") and current["active_stage"] not in stage_ids:
        errors.append(f"CURRENT references unknown stage {current['active_stage']}")
    if current.get("active_phase") and current["active_phase"] not in phase_ids:
        errors.append(f"CURRENT references unknown phase {current['active_phase']}")
    expected_active_stage = active_stages[0] if active_stages else ""
    expected_active_phase = active_phases[0] if active_phases else ""
    if current.get("active_stage", "") != expected_active_stage:
        errors.append("CURRENT active stage does not match stage status")
    if current.get("active_phase", "") != expected_active_phase:
        errors.append("CURRENT active phase does not match phase status")

    for target in current.get("session_targets", []):
        if target.get("status") not in ACTION_STATUSES:
            errors.append(f"{target.get('id')} has invalid action status {target.get('status')}")

    for item in data["vision"]["items"]:
        if item.get("maturity") not in VISION_MATURITIES:
            errors.append(f"{item['id']} has invalid maturity {item.get('maturity')}")
        if item.get("disposition") not in VISION_DISPOSITIONS:
            errors.append(f"{item['id']} has invalid disposition {item.get('disposition')}")
        for related in item.get("related", []):
            if related and related not in known_ids:
                warnings.append(f"{item['id']} references unresolved related ID {related}")
    return errors, warnings


def budget_report(data: dict, outputs: dict[str, str]) -> tuple[list[str], list[str]]:
    cfg = data["config"].get("budgets", {})
    hard_multiplier = float(cfg.get("hard_multiplier", 2.0))
    warnings: list[str] = []
    errors: list[str] = []
    rules = {
        "CURRENT.md": int(cfg.get("current_tokens", 1600)),
        "VISION.md": int(cfg.get("vision_tokens", 12000)),
        "TECHNICAL_STATE.md": int(cfg.get("technical_tokens", 5000)),
    }
    phase_budget = int(cfg.get("phase_tokens", 5500))
    for path, text in outputs.items():
        limit = rules.get(path)
        if "/phases/" in path:
            limit = phase_budget
        if not limit:
            continue
        tokens = approx_tokens(text)
        if tokens > limit:
            warnings.append(f"{path} is about {tokens} tokens; target is {limit}")
        if tokens > int(limit * hard_multiplier):
            errors.append(
                f"{path} is about {tokens} tokens; hard cap is {int(limit * hard_multiplier)}. "
                "Distill it in this same delta: complete, cancel, or supersede finished targets; "
                "resolve stale issues and threads; replace last_session with a shorter summary; "
                "and move detail into the active phase file or lessons."
            )
    return errors, warnings


def _phase_summary(phase: dict | None) -> dict | None:
    if not phase:
        return None
    return {
        "id": phase.get("id"),
        "title": phase.get("title"),
        "status": phase.get("status"),
        "stage": phase.get("stage"),
        "milestones": phase.get("milestones", []),
        "status_reason": phase.get("status_reason", ""),
        "resume_condition": phase.get("resume_condition", ""),
        "counts": {
            "accomplishments": len(phase.get("accomplishments", [])),
            "failures": len(phase.get("failures", [])),
            "decisions": len(phase.get("decisions", [])),
        },
    }


def _stage_summary(stage: dict | None) -> dict | None:
    if not stage:
        return None
    return {
        "id": stage.get("id"),
        "title": stage.get("title"),
        "status": stage.get("status"),
        "intent": stage.get("intent", ""),
        "criteria": stage.get("criteria", []),
    }


def prepare_save(store: Store, session_id: str, full: bool = False) -> dict:
    if not store.exists:
        return {
            "initialized": False,
            "expected_revision": 0,
            "boundary": "new",
            "session_id": session_id,
            "message": "SaveState is not initialized; apply will initialize it transactionally.",
        }
    data = store.load_all()
    outputs = render_all(store, data)
    budget_errors, budget_warnings = budget_report(data, outputs)
    session_warnings: list[str] = []
    if not session_id:
        session_warnings.append(
            "Claude session ID is empty, so automatic session-boundary detection is "
            "disabled. On the first save of this conversation pass boundary=new; "
            "on later saves in the same conversation pass boundary=same."
        )
    revision = data["metadata"].get("revision", 0)
    stage = item_by_id(data["stages"]["items"], data["current"].get("active_stage", ""))
    phase = item_by_id(data["phases"]["items"], data["current"].get("active_phase", ""))
    pending = [i for i in data["pending"]["items"] if i.get("status") == "pending"]
    open_targets = [
        {"id": t.get("id"), "text": t.get("text")}
        for t in data["current"].get("session_targets", [])
        if t.get("status", "open") == "open"
    ]
    result = {
        "initialized": True,
        "session_warnings": session_warnings,
        "expected_revision": revision,
        "active_cycle_id": data["metadata"].get("active_cycle_id", ""),
        "active_session_id": data["metadata"].get("active_claude_session_id", ""),
        "boundary": detect_boundary(data, session_id, "auto"),
        "session_id": session_id,
        "current": data["current"],
        "active_stage": _stage_summary(stage) if not full else stage,
        "active_phase": _phase_summary(phase) if not full else phase,
        "open_target_ids": open_targets,
        "pending_note_ids": [i.get("id") for i in pending],
        "pending_notes": pending,
        "repository": git_context(store.project_root),
        "budget_warnings": budget_warnings,
        "budget_errors": budget_errors,
        "delta_skeleton": {
            "expected_revision": revision,
            "session_summary": "",
            "handoff": {"last_session": []},
            "handoff_ops": [],
        },
    }
    return result


def finalize_save(store: Store, data: dict, session_id: str) -> tuple[dict[str, str], list[str], list[str]]:
    update_repository_context(store, data)
    data["metadata"]["last_saved_at"] = iso_now()
    data["metadata"]["active_claude_session_id"] = session_id or data["metadata"].get("active_claude_session_id", "")
    data["metadata"]["revision"] = int(data["metadata"].get("revision", 0)) + 1
    errors, warnings = validate_data(data)
    outputs = render_all(store, data)
    budget_errors, budget_warnings = budget_report(data, outputs)
    errors.extend(budget_errors)
    warnings.extend(budget_warnings)
    if errors:
        return outputs, errors, warnings
    write_outputs(store, data)
    store.save_all(data)
    return outputs, errors, warnings


def verify_live(store: Store) -> list[str]:
    """Re-read the just-committed live state and prove it is loadable, valid,
    and rendered without drift. Run after every commit; a failure means the
    backup must be restored."""
    try:
        data = store.load_all()
    except Exception as exc:
        return [f"live state is unreadable after commit: {exc}"]
    errors, _ = validate_data(data)
    expected = render_all(store, data)
    for rel, text in expected.items():
        path = store.state_dir / rel
        if not path.exists() or path.read_text(encoding="utf-8", errors="replace") != text:
            errors.append(f"rendered output drifted immediately after commit: {rel}")
            break
    return errors


def write_journal_entry(
    store: Store,
    delta: dict,
    session_id: str,
    boundary: str,
    before_files: dict[str, bytes],
    result_revision: int,
) -> str:
    """Record the applied delta verbatim, with the pre-state content hashes,
    in an append-only journal under history/. The journal is written by this
    small mechanical function, not by the semantic engine paths, so any future
    engine defect leaves a complete, replayable account of every save."""
    entry = {
        "at": iso_now(),
        "session_id": session_id,
        "boundary": boundary,
        "result_revision": result_revision,
        "before_hashes": {
            name: sha256_text(before_files[name].decode("utf-8", errors="replace"))
            for name in sorted(before_files)
            if name.startswith("data/")
        },
        "delta": delta,
    }
    journal_dir = store.history_dir / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / f"{compact_stamp()}-rev{result_revision:05d}.json"
    write_json(path, entry)
    return str(path.relative_to(store.state_dir))
