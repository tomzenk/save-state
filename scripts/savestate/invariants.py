from __future__ import annotations

"""Independent conservation auditor.

The engine implements state transformations. This module implements none of
them. It receives the state before a delta, the delta itself, and the state
after, computes the change surface the delta authorizes, and reports every
change outside that surface as a violation. The caller rejects the save when
any violation exists.

The point is architectural: SaveState's history includes engines that claimed
merge semantics and silently violated them. With this auditor in the apply
path, a silent-forgetting bug has to exist in two independent implementations
at once before it can reach disk. Keep this module free of any code that
builds or mutates state; it may only read and compare.
"""

from typing import Any

TARGET_STATUS_OPS = {
    "complete_target",
    "block_target",
    "cancel_target",
    "supersede_target",
    "carry_forward_target",
}


def _resume_action(value: Any) -> Any:
    return value


def audit(before: dict, delta: dict, after: dict) -> list[str]:
    violations: list[str] = []

    handoff = delta.get("handoff") or {}
    hops = [op for op in (delta.get("handoff_ops") or []) if isinstance(op, dict)]
    op_kinds = {str(op.get("op")) for op in hops}
    goal_ops = [op for op in (delta.get("goal_ops") or []) if isinstance(op, dict)]
    vision_ops = [op for op in (delta.get("vision_ops") or []) if isinstance(op, dict)]

    target_op_ids = {str(op.get("id")) for op in hops if op.get("op") in TARGET_STATUS_OPS}
    targets_replaced = "session_targets" in handoff
    issues_replaced = "relevant_issues" in handoff
    threads_replaced = "open_threads" in handoff
    last_replaced = "last_session" in handoff
    def _norm(value: str) -> str:
        return " ".join(str(value).split())

    resolved_issues = {_norm(op.get("text") or "") for op in hops if op.get("op") == "resolve_issue"}
    resolved_threads = {_norm(op.get("text") or "") for op in hops if op.get("op") == "resolve_thread"}
    resume_auth = "resume_point" in handoff or "set_resume_point" in op_kinds
    active_path_auth = bool(goal_ops) or "set_active_path" in op_kinds or "active_stage" in handoff or "active_phase" in handoff
    recent_auth = bool(goal_ops) or "clear_recent_phase_changes" in op_kinds
    tech_auth = set((delta.get("technical") or {}).keys()) | {"updated_at", "repository"}
    goal_op_ids = {str(op.get("id") or "") for op in goal_ops} | {str(op.get("stage") or "") for op in goal_ops}
    vision_op_ids = {str(op.get("id") or "") for op in vision_ops if op.get("op") != "add"}
    vision_add_present = any(op.get("op") == "add" for op in vision_ops)
    resolved_note_ids = {str(x) for x in (delta.get("resolve_notes") or [])}

    narrative = [
        item
        for key in ("accomplished", "failed", "decisions")
        for item in (delta.get(key) or [])
        if isinstance(item, dict)
    ]
    narrative_phase_ids = {str(item.get("phase")) for item in narrative if item.get("phase")}
    if narrative and any(not item.get("phase") for item in narrative):
        active_before = str(before["current"].get("active_phase") or "")
        if active_before:
            narrative_phase_ids.add(active_before)

    b_cur, a_cur = before["current"], after["current"]
    cycle_changed = str(before["metadata"].get("active_cycle_id") or "") != str(
        after["metadata"].get("active_cycle_id") or ""
    )
    if cycle_changed:
        # A new cycle just opened; boundary hygiene may rotate the hot
        # recency fields. Everything trimmed was archived in the boundary
        # snapshot and remains in the event log.
        recent_auth = True

    # Revision advances by exactly one per apply.
    b_rev = int(before["metadata"].get("revision", 0))
    a_rev = int(after["metadata"].get("revision", 0))
    if a_rev != b_rev + 1:
        violations.append(f"revision moved {b_rev} -> {a_rev}; an apply must advance it by exactly 1")

    # Counters never decrease; a cycle is never silently discarded.
    for prefix, value in (before["metadata"].get("counters") or {}).items():
        if int((after["metadata"].get("counters") or {}).get(prefix, 0)) < int(value):
            violations.append(f"ID counter {prefix} decreased")
    if before["metadata"].get("active_cycle_id") and not after["metadata"].get("active_cycle_id"):
        violations.append("active cycle ID was emptied")

    # Session targets: existence and status are conserved unless the delta
    # says otherwise.
    if not targets_replaced:
        b_targets = {str(t.get("id")): t for t in b_cur.get("session_targets", [])}
        a_targets = {str(t.get("id")): t for t in a_cur.get("session_targets", [])}
        for tid, t in b_targets.items():
            if tid not in a_targets:
                violations.append(f"target {tid} disappeared without session_targets replacement")
            elif tid not in target_op_ids and a_targets[tid] != t:
                violations.append(f"target {tid} changed without an operation naming it")
        if not ("add_target" in op_kinds) and set(a_targets) - set(b_targets):
            extra = sorted(set(a_targets) - set(b_targets))
            violations.append(f"targets appeared without add_target: {', '.join(extra)}")

    # Issues and threads: entries survive until explicitly resolved.
    if not issues_replaced:
        for text in b_cur.get("relevant_issues", []):
            if text not in a_cur.get("relevant_issues", []) and _norm(text) not in resolved_issues:
                violations.append(f"issue dropped without resolve_issue: {text!r}")
        if "add_issue" not in op_kinds:
            for text in a_cur.get("relevant_issues", []):
                if text not in b_cur.get("relevant_issues", []):
                    violations.append(f"issue appeared without add_issue: {text!r}")
    if not threads_replaced:
        for text in b_cur.get("open_threads", []):
            if text not in a_cur.get("open_threads", []) and _norm(text) not in resolved_threads:
                violations.append(f"thread dropped without resolve_thread: {text!r}")
        if "add_thread" not in op_kinds:
            for text in a_cur.get("open_threads", []):
                if text not in b_cur.get("open_threads", []):
                    violations.append(f"thread appeared without add_thread: {text!r}")

    # The resume point changes only when explicitly supplied.
    if not resume_auth and _resume_action(a_cur.get("resume_point")) != _resume_action(b_cur.get("resume_point")):
        violations.append("resume point changed without being supplied")

    # last_session is append-only unless replaced wholesale or rolled at a
    # cycle boundary.
    if not last_replaced and not cycle_changed:
        b_last, a_last = b_cur.get("last_session", []), a_cur.get("last_session", [])
        if a_last[: len(b_last)] != b_last:
            violations.append("last_session lost or reordered prior entries")
        elif len(a_last) > len(b_last) and "append_last_session" not in op_kinds:
            violations.append("last_session grew without append_last_session")

    for key in ("technical_summary", "boot_sequence"):
        if key not in handoff and a_cur.get(key) != b_cur.get(key):
            violations.append(f"{key} changed without being supplied")

    if not active_path_auth:
        for key in ("active_stage", "active_phase"):
            if a_cur.get(key) != b_cur.get(key):
                violations.append(f"{key} changed without a goal operation")
    if not recent_auth and a_cur.get("recent_phase_changes") != b_cur.get("recent_phase_changes"):
        violations.append("recent_phase_changes changed without authorization")

    # Technical state: sparse patch only touches supplied keys.
    for key, value in before["technical"].items():
        if key not in tech_auth and after["technical"].get(key) != value:
            violations.append(f"technical.{key} changed without being supplied")

    # Vision items are never deleted; unauthorized items are untouched.
    a_vision = {str(i.get("id")): i for i in after["vision"]["items"]}
    for item in before["vision"]["items"]:
        vid = str(item.get("id"))
        if vid not in a_vision:
            violations.append(f"vision item {vid} was deleted")
        elif vid not in vision_op_ids and a_vision[vid] != item:
            violations.append(f"vision item {vid} changed without an operation naming it")
    if not vision_add_present:
        b_ids = {str(i.get("id")) for i in before["vision"]["items"]}
        for vid in a_vision:
            if vid not in b_ids:
                violations.append(f"vision item {vid} appeared without an add operation")

    # Stages and phases: identity is permanent; unauthorized records are
    # untouched, and narrative may enrich a phase but never restate its
    # status, parent, or title.
    a_stages = {str(i.get("id")): i for i in after["stages"]["items"]}
    for item in before["stages"]["items"]:
        sid = str(item.get("id"))
        if sid not in a_stages:
            violations.append(f"stage {sid} was deleted")
        elif sid not in goal_op_ids and a_stages[sid] != item:
            violations.append(f"stage {sid} changed without an operation naming it")
    a_phases = {str(i.get("id")): i for i in after["phases"]["items"]}
    for item in before["phases"]["items"]:
        pid = str(item.get("id"))
        if pid not in a_phases:
            violations.append(f"phase {pid} was deleted")
            continue
        if pid in goal_op_ids:
            continue
        changed = a_phases[pid] != item
        if changed and pid in narrative_phase_ids:
            for key in ("status", "stage", "title"):
                if a_phases[pid].get(key) != item.get(key):
                    violations.append(f"phase {pid} {key} changed by narrative without a goal operation")
        elif changed:
            violations.append(f"phase {pid} changed without an operation or narrative naming it")

    # Lessons and events are append-only with immutable prior entries.
    for collection in ("lessons", "events"):
        b_items = before[collection]["items"]
        a_items = after[collection]["items"]
        if a_items[: len(b_items)] != b_items:
            violations.append(f"{collection} lost or rewrote prior entries")

    # Pending notes: text immutable; resolution only by exact ID.
    a_notes = {str(i.get("id")): i for i in after["pending"]["items"]}
    for item in before["pending"]["items"]:
        nid = str(item.get("id"))
        if nid not in a_notes:
            violations.append(f"pending note {nid} was deleted")
            continue
        if a_notes[nid].get("text") != item.get("text"):
            violations.append(f"pending note {nid} text changed")
        b_status, a_status = item.get("status"), a_notes[nid].get("status")
        if b_status != a_status and not (b_status == "pending" and a_status == "resolved" and nid in resolved_note_ids):
            violations.append(f"pending note {nid} status changed without resolve_notes")
    b_note_ids = {str(i.get("id")) for i in before["pending"]["items"]}
    for nid in a_notes:
        if nid not in b_note_ids:
            violations.append(f"pending note {nid} appeared during an apply")

    return violations
