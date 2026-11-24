from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .constants import STATE_DIRNAME
from .engine import (
    SaveStateError,
    add_event,
    alloc,
    apply_delta,
    budget_report,
    cycle_is_fresh,
    detect_boundary,
    finalize_save,
    item_by_id,
    open_cycle,
    prepare_save,
    snapshot_bundle,
    validate_data,
    verify_live,
    write_journal_entry,
)
from .invariants import audit
from .gitutils import ensure_gitignore, is_repo, state_only_commit
from .render import (
    build_index,
    render_all,
    render_current,
    render_pending,
    write_outputs,
)
from .store import (
    StateLock,
    Store,
    commit_transaction,
    discard_backup,
    restore_backup,
    transaction_store,
)
from .util import deep_copy, file_tree, find_project_root, iso_now, json_text, read_json, run, write_json, write_text


def emit(value: Any, as_json: bool = True) -> None:
    if as_json:
        print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(value)


def resolve_store(root: str | None) -> Store:
    project_root = find_project_root(Path(root) if root else Path.cwd())
    return Store(project_root)


def load_delta(source: str) -> dict:
    if source == "-":
        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SaveStateError(f"Invalid delta JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SaveStateError("Delta must be a JSON object")
    return value


def unified_state_diff(before: dict[str, bytes], after: dict[str, bytes], max_lines: int = 600) -> dict:
    changed = sorted(set(before) | set(after))
    changed = [path for path in changed if before.get(path) != after.get(path)]
    diff_lines: list[str] = []
    for path in changed:
        old = before.get(path, b"").decode("utf-8", errors="replace").splitlines()
        new = after.get(path, b"").decode("utf-8", errors="replace").splitlines()
        if path.endswith((".md", ".json")):
            diff_lines.extend(
                difflib.unified_diff(
                    old,
                    new,
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )
        if len(diff_lines) >= max_lines:
            diff_lines.append("[diff truncated]")
            break
    return {"changed_files": changed, "diff": "\n".join(diff_lines[:max_lines])}


def commit_and_verify(live: Store, temp_state: Path) -> None:
    """Swap the transaction in, then prove the landed state is loadable,
    valid, and drift-free before releasing the pre-commit backup. On any
    verification failure the previous state is restored and the operation
    fails loudly. A save is finished when the landed bytes are proven good,
    not merely when they land."""
    backup = commit_transaction(live.project_root, temp_state)
    problems = verify_live(live)
    if problems:
        restore_backup(live.project_root, backup)
        raise SaveStateError(
            "Post-commit verification failed and the previous state was restored: "
            + "; ".join(problems)
        )
    discard_backup(backup)


def cmd_init(args) -> int:
    live = resolve_store(args.root)
    with StateLock(live.project_root):
        if live.exists and not args.force:
            emit({"initialized": False, "reason": "already-initialized", "path": str(live.state_dir)})
            return 0
        before = file_tree(live.state_dir, exclude_top={"history"})
        with transaction_store(live.project_root) as (temp, _):
            if args.force and live.exists:
                shutil.rmtree(temp.state_dir)
                temp.initialize()
            data = temp.load_all()
            if not data["metadata"].get("active_cycle_id"):
                open_cycle(data, args.session_id or "", args.label or "initial")
            outputs, errors, warnings = finalize_save(temp, data, args.session_id or "")
            if errors:
                raise SaveStateError("; ".join(errors))
            after = file_tree(temp.state_dir, exclude_top={"history"})
            diff = unified_state_diff(before, after)
            if args.dry_run:
                emit({"dry_run": True, **diff, "warnings": warnings})
                return 0
            commit_and_verify(live, temp.state_dir)
        gitignore_updated = ensure_gitignore(live.project_root)
        result = {"initialized": True, "path": str(live.state_dir), "gitignore_updated": gitignore_updated, "warnings": warnings, "changed_files": diff["changed_files"]}
        if args.show_diff:
            result["diff"] = diff["diff"]
        emit(result)
    return 0


def cmd_load(args) -> int:
    store = resolve_store(args.root)
    if not store.exists:
        emit({"initialized": False, "path": str(store.state_dir), "message": "No SaveState exists."})
        return 0
    data = store.load_all()
    if args.json:
        emit({"metadata": data["metadata"], "current": data["current"]})
        return 0
    current_text = (store.state_dir / "CURRENT.md").read_text(encoding="utf-8")
    if args.lite:
        current = data["current"]
        stage = item_by_id(data["stages"]["items"], current.get("active_stage", "")) or {}
        phase = item_by_id(data["phases"]["items"], current.get("active_phase", "")) or {}
        resume = current.get("resume_point") or {}
        action = resume if isinstance(resume, str) else resume.get("action", "")
        targets = current.get("session_targets", [])
        held = [
            phase_item
            for phase_item in data["phases"]["items"]
            if phase_item.get("status") in {"paused", "blocked"}
        ]
        result = {
            "active_stage": f"{stage.get('id', '')} - {stage.get('title', '')}".strip(" -"),
            "active_phase": f"{phase.get('id', '')} - {phase.get('title', '')}".strip(" -"),
            "last_session": current.get("last_session", []),
            "resume_point": action,
            "resume_detail": resume,
            "session_targets": [t for t in targets if t.get("status", "open") == "open"],
            "blocked_targets": [t for t in targets if t.get("status") == "blocked"],
            "relevant_issues": current.get("relevant_issues", []),
            "paused_or_blocked_phases": [
                {
                    "id": item.get("id"),
                    "status": item.get("status"),
                    "reason": item.get("status_reason", ""),
                    "resume_condition": item.get("resume_condition", ""),
                }
                for item in held
            ],
            "revision": data["metadata"].get("revision", 0),
        }
        emit(result)
    else:
        print(current_text, end="")
    return 0


def cmd_save_prep(args) -> int:
    store = resolve_store(args.root)
    emit(prepare_save(store, args.session_id or "", full=args.full))
    return 0


def cmd_revision(args) -> int:
    store = resolve_store(args.root)
    if not store.exists:
        emit({"initialized": False, "revision": 0})
        return 0
    meta = store.load_all()["metadata"]
    emit({
        "initialized": True,
        "revision": meta.get("revision", 0),
        "active_cycle_id": meta.get("active_cycle_id", ""),
        "last_saved_at": meta.get("last_saved_at", ""),
    })
    return 0


def cmd_apply(args) -> int:
    live = resolve_store(args.root)
    delta = load_delta(args.source)
    session_id = args.session_id or str(delta.get("session_id") or "")
    expected = delta.get("expected_revision")
    live_revision = 0
    if live.exists:
        live_revision = int(live.load_all()["metadata"].get("revision", 0))
    if expected is None:
        raise SaveStateError("Delta requires expected_revision from save-prep")
    if int(expected) != live_revision:
        raise SaveStateError(
            f"Stale delta: expected revision {expected}, current revision is {live_revision}. "
            "A note, checkpoint, doctor --repair, or another session's save advanced it. "
            "Run save-prep again, or `state.py revision` for the number alone."
        )

    with StateLock(live.project_root):
        # Recheck after acquiring the lock.
        locked_revision = int(live.load_all()["metadata"].get("revision", 0)) if live.exists else 0
        if int(expected) != locked_revision:
            raise SaveStateError(
                f"State changed while waiting for the lock: expected {expected}, now {locked_revision}."
            )

        before = file_tree(live.state_dir, exclude_top={"history"})
        archive = None
        with transaction_store(live.project_root) as (temp, _):
            data = temp.load_all()
            audit_before = deep_copy(data)
            reused_fresh_cycle = delta.get("boundary") == "new" and cycle_is_fresh(data)
            if reused_fresh_cycle:
                delta = {**delta, "boundary": "same"}
            boundary = detect_boundary(data, session_id, delta.get("boundary", "auto"))
            if boundary == "new" and data["metadata"].get("active_cycle_id"):
                archive = snapshot_bundle(temp, data, "session-boundary", str(delta.get("cycle_label") or ""))
            semantic_warnings = apply_delta(data, delta, session_id)
            if reused_fresh_cycle:
                semantic_warnings.append(
                    "boundary=new was requested, but the active cycle had no saves yet "
                    "(opened by init), so this save went into it instead of opening another."
                )
            if not session_id and delta.get("boundary", "auto") == "auto":
                semantic_warnings.append(
                    "Claude session ID was empty and boundary=auto resolved to the "
                    "existing cycle. If this is actually a new conversation, save "
                    "again with boundary=new so the prior cycle is archived."
                )
            outputs, errors, warnings = finalize_save(temp, data, session_id)
            warnings = [*semantic_warnings, *warnings]
            if errors:
                raise SaveStateError("Validation failed: " + "; ".join(errors))
            conservation = audit(audit_before, delta, data)
            if conservation:
                raise SaveStateError(
                    "Conservation audit failed; the engine produced changes the delta "
                    "does not authorize and the save was rejected: "
                    + "; ".join(conservation)
                )
            journal_path = write_journal_entry(
                temp, delta, session_id, boundary, before, int(data["metadata"].get("revision", 0))
            )
            after = file_tree(temp.state_dir, exclude_top={"history"})
            diff = unified_state_diff(before, after)
            if args.dry_run:
                emit(
                    {
                        "dry_run": True,
                        "nothing_written": True,
                        "archive_would_be_created": archive,
                        "revision_would_be": data["metadata"].get("revision"),
                        "warnings": warnings,
                        **diff,
                    }
                )
                return 0
            commit_and_verify(live, temp.state_dir)

        ensure_gitignore(live.project_root)
        saved_state = live.load_all()
        commit_result = {"committed": False, "reason": "disabled"}
        commit_requested = delta.get("git", {}).get(
            "commit", saved_state["config"].get("git", {}).get("auto_commit_state", True)
        )
        if commit_requested:
            summary = str(delta.get("session_summary") or "update project state").strip()
            message = str(delta.get("git", {}).get("message") or f"chore(savestate): {summary}")
            commit_result = state_only_commit(live.project_root, STATE_DIRNAME, message)

        current = saved_state["current"]
        result = {
            "saved": True,
            "archive_created": archive,
            "journal_entry": journal_path,
            "revision": saved_state["metadata"].get("revision"),
            "warnings": warnings,
            "git": commit_result,
            "immediate_resume_point": current.get("resume_point"),
            "changed_files": diff["changed_files"],
        }
        if args.show_diff:
            result["diff"] = diff["diff"]
        emit(result)
    return 0


def cmd_note(args) -> int:
    live = resolve_store(args.root)
    text = args.text
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        raise SaveStateError("note requires --text or stdin")
    with StateLock(live.project_root):
        with transaction_store(live.project_root) as (temp, _):
            data = temp.load_all()
            note_id = alloc(data["metadata"], "NOTE")
            data["pending"]["items"].append(
                {
                    "id": note_id,
                    "text": text,
                    "status": "pending",
                    "created_at": iso_now(),
                    "claude_session_id": args.session_id or "",
                }
            )
            data["metadata"]["revision"] = int(data["metadata"].get("revision", 0)) + 1
            # Only pending state and metadata change. No cycle is opened.
            write_outputs(temp, data)
            temp.save_all(data)
            commit_and_verify(live, temp.state_dir)
        commit_result = {"committed": False, "reason": "notes-not-auto-committed"}
        config = live.load_all()["config"]
        if args.commit or config.get("git", {}).get("commit_notes", False):
            commit_result = state_only_commit(
                live.project_root,
                STATE_DIRNAME,
                f"chore(savestate): capture {note_id}",
            )
        emit({"noted": True, "id": note_id, "git": commit_result})
    return 0


def cmd_checkpoint(args) -> int:
    live = resolve_store(args.root)
    if not live.exists:
        raise SaveStateError("SaveState is not initialized")
    with StateLock(live.project_root):
        with transaction_store(live.project_root) as (temp, _):
            data = temp.load_all()
            path = snapshot_bundle(temp, data, "checkpoint", args.label or "")
            add_event(data, "checkpoint", f"Created checkpoint {path}", related=[data["metadata"].get("active_cycle_id", "")])
            outputs, errors, warnings = finalize_save(temp, data, args.session_id or data["metadata"].get("active_claude_session_id", ""))
            if errors:
                raise SaveStateError("; ".join(errors))
            commit_and_verify(live, temp.state_dir)
        config = live.load_all()["config"]
        commit_result = {"committed": False, "reason": "disabled"}
        if config.get("git", {}).get("auto_commit_state", True):
            commit_result = state_only_commit(live.project_root, STATE_DIRNAME, f"chore(savestate): checkpoint {args.label or ''}".strip())
        emit({"checkpoint": path, "warnings": warnings, "git": commit_result})
    return 0


def cmd_closeout(args) -> int:
    live = resolve_store(args.root)
    if not live.exists:
        raise SaveStateError("SaveState is not initialized")
    with StateLock(live.project_root):
        with transaction_store(live.project_root) as (temp, _):
            data = temp.load_all()
            path = snapshot_bundle(temp, data, "explicit-closeout", args.label or "")
            old_cycle = data["metadata"].get("active_cycle_id", "")
            new_cycle = open_cycle(data, args.session_id or data["metadata"].get("active_claude_session_id", ""), args.label or "")
            add_event(data, "cycle", f"Closed {old_cycle} and opened {new_cycle}", related=[old_cycle, new_cycle])
            outputs, errors, warnings = finalize_save(temp, data, args.session_id or "")
            if errors:
                raise SaveStateError("; ".join(errors))
            commit_and_verify(live, temp.state_dir)
        config = live.load_all()["config"]
        commit_result = {"committed": False, "reason": "disabled"}
        if config.get("git", {}).get("auto_commit_state", True):
            commit_result = state_only_commit(live.project_root, STATE_DIRNAME, f"chore(savestate): close out {old_cycle}")
        emit({"closed_cycle": old_cycle, "new_cycle": new_cycle, "archive": path, "warnings": warnings, "git": commit_result})
    return 0


def cmd_vision(args) -> int:
    store = resolve_store(args.root)
    if not store.exists:
        raise SaveStateError("SaveState is not initialized")
    data = store.load_all()
    items = data["vision"]["items"]
    if args.id:
        item = item_by_id(items, args.id)
        emit(item or {"found": False, "id": args.id})
        return 0
    tags = {tag.lower() for tag in (args.tags or [])}
    maturity = args.maturity
    disposition = args.disposition
    results = []
    for item in items:
        if tags and not tags.intersection({str(x).lower() for x in item.get("tags", [])}):
            continue
        if maturity and item.get("maturity") != maturity:
            continue
        if disposition and item.get("disposition") != disposition:
            continue
        results.append(item)
    emit({"count": len(results), "items": results})
    return 0


def cmd_phase(args) -> int:
    store = resolve_store(args.root)
    if not store.exists:
        raise SaveStateError("SaveState is not initialized")
    data = store.load_all()
    phase = item_by_id(data["phases"]["items"], args.id)
    emit(phase or {"found": False, "id": args.id})
    return 0


def cmd_find(args) -> int:
    store = resolve_store(args.root)
    if not store.exists:
        raise SaveStateError("SaveState is not initialized")
    data = store.load_all()
    query = (args.text or "").lower()
    results: list[dict] = []
    if args.id:
        record = data["index"].get("by_id", {}).get(args.id)
        emit({"id": args.id, "record": record})
        return 0
    if args.file:
        ids = data["index"].get("by_file", {}).get(args.file, [])
        emit({"file": args.file, "ids": ids})
        return 0
    if args.tag:
        ids = data["index"].get("by_tag", {}).get(args.tag.lower(), [])
        emit({"tag": args.tag, "ids": ids})
        return 0
    for collection in ("vision", "stages", "phases", "lessons", "events", "pending"):
        for item in data[collection]["items"]:
            haystack = json.dumps(item, ensure_ascii=False).lower()
            if query and query in haystack:
                results.append({"collection": collection, "item": item})
    emit({"query": args.text, "count": len(results), "results": results[: args.limit]})
    return 0


def cmd_history(args) -> int:
    store = resolve_store(args.root)
    if not store.exists:
        raise SaveStateError("SaveState is not initialized")
    if args.show:
        if Path(args.show).name != args.show:
            raise SaveStateError("history --show accepts a snapshot directory name, not a path")
        target = store.history_dir / args.show
        if not target.exists():
            raise SaveStateError(f"Unknown history snapshot: {args.show}")
        manifest = read_json(target / "manifest.json") if (target / "manifest.json").exists() else {}
        emit({"snapshot": args.show, "manifest": manifest, "files": sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())})
    elif args.journal:
        journal_dir = store.history_dir / "journal"
        entries = []
        if journal_dir.exists():
            for path in sorted(journal_dir.glob("*.json"), reverse=True)[: args.limit]:
                record = read_json(path)
                entries.append(
                    {
                        "name": path.name,
                        "at": record.get("at", ""),
                        "revision": record.get("result_revision"),
                        "boundary": record.get("boundary", ""),
                        "session_id": record.get("session_id", ""),
                    }
                )
        emit({"count": len(entries), "journal": entries})
    else:
        snapshots = []
        if store.history_dir.exists():
            for path in sorted(store.history_dir.iterdir(), reverse=True):
                if path.is_dir() and path.name != "journal":
                    manifest = read_json(path / "manifest.json") if (path / "manifest.json").exists() else {}
                    snapshots.append({"name": path.name, "manifest": manifest})
        emit({"count": len(snapshots), "snapshots": snapshots})
    return 0


def orphan_report(store: Store) -> tuple[list[str], list[str]]:
    """Detect leftovers from interrupted operations at the project root."""
    errors: list[str] = []
    warnings: list[str] = []
    root = store.project_root
    backups = sorted(p.name for p in root.glob(".savestate.backup-*") if p.is_dir())
    txns = sorted(p.name for p in root.glob(".savestate-txn-*") if p.is_dir())
    lock = root / ".savestate.lock"
    if backups and not store.exists:
        errors.append(
            "Live .savestate/ is missing but a backup from an interrupted operation "
            f"exists: {backups[0]}. Restore it with: mv {backups[0]} .savestate"
        )
    elif backups:
        warnings.append(
            "Orphaned backup from an interrupted operation: "
            + ", ".join(backups)
            + ". The live state is present; delete the backup after confirming it holds nothing newer."
        )
    if txns:
        warnings.append(
            "Leftover transaction directories: " + ", ".join(txns) + ". Safe to delete."
        )
    if lock.exists():
        import time as _time

        age = int(_time.time() - lock.stat().st_mtime)
        warnings.append(
            f".savestate.lock exists (about {age}s old). If no save is currently "
            "running, delete it."
        )
    return errors, warnings


def missing_commits(root: Path, data: dict) -> list[tuple[str, str]]:
    if not is_repo(root):
        return []
    missing = []
    for phase in data["phases"]["items"]:
        for commit in phase.get("commits", []):
            check = run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], root)
            if check.returncode != 0:
                missing.append((str(commit), str(phase.get("id"))))
    return missing


def doctor_report(store: Store) -> dict:
    orphan_errors, orphan_warnings = orphan_report(store)
    if not store.exists:
        return {
            "initialized": False,
            "errors": orphan_errors,
            "warnings": ["SaveState is not initialized", *orphan_warnings],
        }
    try:
        data = store.load_all()
    except Exception as exc:
        return {"initialized": True, "errors": [str(exc)], "warnings": []}
    errors, warnings = validate_data(data)
    expected = render_all(store, data)
    drift = []
    for rel, text in expected.items():
        path = store.state_dir / rel
        if not path.exists() or path.read_text(encoding="utf-8", errors="replace") != text:
            drift.append(rel)
    budget_errors, budget_warnings = budget_report(data, expected)
    errors.extend(budget_errors)
    warnings.extend(budget_warnings)
    if drift:
        warnings.append("Generated Markdown drift detected: " + ", ".join(drift))
    missing = missing_commits(store.project_root, data)
    if missing:
        warnings.append(
            "Recorded commits not found in the repository (history rewritten or "
            "not yet fetched): " + ", ".join(f"{h} ({where})" for h, where in missing)
        )
    errors.extend(orphan_errors)
    warnings.extend(orphan_warnings)
    return {
        "initialized": True,
        "revision": data["metadata"].get("revision", 0),
        "errors": errors,
        "warnings": warnings,
        "drift": drift,
    }


def cmd_doctor(args) -> int:
    live = resolve_store(args.root)
    report = doctor_report(live)
    if args.repair and live.exists:
        needs_repair = bool(report.get("drift"))
        with StateLock(live.project_root):
            with transaction_store(live.project_root) as (temp, _):
                data = temp.load_all()
                data["index"] = build_index(data)
                if needs_repair:
                    data["metadata"]["revision"] = int(data["metadata"].get("revision", 0)) + 1
                    data["metadata"]["last_saved_at"] = iso_now()
                write_outputs(temp, data)
                temp.save_all(data)
                commit_and_verify(live, temp.state_dir)
        report = doctor_report(live)
        report["repaired"] = needs_repair
    emit(report)
    return 0 if not report.get("errors") else 1


def cmd_commit(args) -> int:
    store = resolve_store(args.root)
    message = args.message or "chore(savestate): update project state"
    emit(state_only_commit(store.project_root, STATE_DIRNAME, message))
    return 0


def cmd_hook(args) -> int:
    try:
        payload = {} if sys.stdin.isatty() else json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    cwd = Path(payload.get("cwd") or args.root or os.getcwd())
    store = Store(find_project_root(cwd))
    if not store.exists:
        return 0
    data = store.load_all()
    mode = data["config"].get("briefing", {}).get("session_start", "pointer")
    if mode == "off":
        return 0
    current = data["current"]
    if mode == "full":
        text = (store.state_dir / "CURRENT.md").read_text(encoding="utf-8")
        limit = int(data["config"].get("briefing", {}).get("full_max_chars", 9000))
        context = text[:limit]
    else:
        stage = item_by_id(data["stages"]["items"], current.get("active_stage", "")) or {}
        phase = item_by_id(data["phases"]["items"], current.get("active_phase", "")) or {}
        resume = current.get("resume_point") or {}
        action = resume if isinstance(resume, str) else resume.get("action", "")
        context = (
            "A SaveState exists for this project. Do not begin work automatically. "
            "When the user asks for the current state or to resume, use the save-state skill.\n"
            f"Active stage: {stage.get('id', '')} - {stage.get('title', '')}\n"
            f"Active phase: {phase.get('id', '')} - {phase.get('title', '')}\n"
            f"Immediate resume point: {action or 'not set'}\n"
            f"Source: {store.state_dir / 'CURRENT.md'}"
        )
        limit = int(data["config"].get("briefing", {}).get("pointer_max_chars", 1800))
        context = context[:limit]
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SaveState v3 transactional project continuity engine")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str):
        item = sub.add_parser(name)
        item.add_argument("--root", default=None)
        return item

    init = command("init")
    init.add_argument("--session-id", default="")
    init.add_argument("--label", default="")
    init.add_argument("--force", action="store_true")
    init.add_argument("--dry-run", action="store_true")
    init.add_argument("--show-diff", action="store_true")
    init.set_defaults(func=cmd_init)

    load = command("load")
    load.add_argument("--lite", action="store_true")
    load.add_argument("--json", action="store_true")
    load.set_defaults(func=cmd_load)

    prep = command("save-prep")
    prep.add_argument("--session-id", default="")
    prep.add_argument("--full", action="store_true")
    prep.set_defaults(func=cmd_save_prep)

    revision = command("revision")
    revision.set_defaults(func=cmd_revision)

    apply_cmd = command("apply")
    apply_cmd.add_argument("source", nargs="?", default="-")
    apply_cmd.add_argument("--session-id", default="")
    apply_cmd.add_argument("--dry-run", action="store_true")
    apply_cmd.add_argument("--show-diff", action="store_true")
    apply_cmd.set_defaults(func=cmd_apply)

    note = command("note")
    note.add_argument("--text", default="")
    note.add_argument("--session-id", default="")
    note.add_argument("--commit", action="store_true")
    note.set_defaults(func=cmd_note)

    checkpoint = command("checkpoint")
    checkpoint.add_argument("--label", default="")
    checkpoint.add_argument("--session-id", default="")
    checkpoint.set_defaults(func=cmd_checkpoint)

    closeout = command("closeout")
    closeout.add_argument("--label", default="")
    closeout.add_argument("--session-id", default="")
    closeout.set_defaults(func=cmd_closeout)

    vision = command("vision")
    vision.add_argument("--id", default="")
    vision.add_argument("--tags", nargs="*", default=[])
    vision.add_argument("--maturity", default="")
    vision.add_argument("--disposition", default="")
    vision.set_defaults(func=cmd_vision)

    phase = command("phase")
    phase.add_argument("id")
    phase.set_defaults(func=cmd_phase)

    find = command("find")
    find.add_argument("--id", default="")
    find.add_argument("--file", default="")
    find.add_argument("--tag", default="")
    find.add_argument("--text", default="")
    find.add_argument("--limit", type=int, default=20)
    find.set_defaults(func=cmd_find)

    history = command("history")
    history.add_argument("--show", default="")
    history.add_argument("--journal", action="store_true")
    history.add_argument("--limit", type=int, default=20)
    history.set_defaults(func=cmd_history)

    doctor = command("doctor")
    doctor.add_argument("--repair", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    commit = command("commit")
    commit.add_argument("--message", default="")
    commit.set_defaults(func=cmd_commit)

    hook = command("hook")
    hook.set_defaults(func=cmd_hook)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (SaveStateError, RuntimeError, ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
