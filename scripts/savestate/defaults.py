from __future__ import annotations

from .constants import FORMAT_VERSION, PREFIXES


def default_config() -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "git": {
            "auto_commit_state": True,
            "commit_notes": False,
        },
        "briefing": {
            "session_start": "pointer",
            "pointer_max_chars": 1800,
            "full_max_chars": 9000,
        },
        "budgets": {
            "current_tokens": 1600,
            "vision_tokens": 12000,
            "technical_tokens": 5000,
            "phase_tokens": 5500,
            "hard_multiplier": 2.0,
        },
        "snapshot": {
            "mode": "working-set",
        },
    }


def default_metadata() -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "revision": 0,
        "active_cycle_id": "",
        "active_claude_session_id": "",
        "cycle_started_at": "",
        "last_saved_at": "",
        "last_snapshot": "",
        "last_state_commit": "",
        "counters": {prefix: 0 for prefix in PREFIXES},
        "render_hashes": {},
    }


def default_current() -> dict:
    return {
        "active_stage": "",
        "active_phase": "",
        "last_session": [],
        "resume_point": {
            "action": "",
            "location": "",
            "start_with": "",
            "expected": "",
            "verify": "",
        },
        "session_targets": [],
        "relevant_issues": [],
        "open_threads": [],
        "technical_summary": "",
        "boot_sequence": [],
        "recent_phase_changes": [],
        "repository": {
            "branch": "",
            "head": "",
            "worktree": "unknown",
            "changed_files": [],
        },
    }


def default_vision() -> dict:
    return {"items": []}


def default_stages() -> dict:
    return {"items": []}


def default_phases() -> dict:
    return {"items": []}


def default_technical() -> dict:
    return {
        "summary": "",
        "updated_at": "",
        "architecture": [],
        "files": [],
        "tests": [],
        "environment": [],
        "constraints": [],
        "known_issues": [],
        "repository": {},
    }


def default_lessons() -> dict:
    return {"items": []}


def default_pending() -> dict:
    return {"items": []}


def default_events() -> dict:
    return {"items": []}


def default_index() -> dict:
    return {
        "by_id": {},
        "by_tag": {},
        "by_file": {},
        "by_text": {},
    }
