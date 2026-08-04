from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def iso_now() -> str:
    return now().isoformat(timespec="seconds")


def month_key(value: str | None = None) -> str:
    if value:
        try:
            return dt.datetime.fromisoformat(value).strftime("%Y-%m")
        except ValueError:
            pass
    return now().strftime("%Y-%m")


def compact_stamp() -> str:
    return now().strftime("%Y-%m-%d_%H-%M-%S")


def slugify(value: str, limit: int = 64, fallback: str = "item") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return (slug or fallback)[:limit]


def approx_tokens(text: str) -> int:
    # Conservative approximation for English/code/Markdown.
    return max(1, (len(text) + 3) // 4)


def deep_copy(value: Any) -> Any:
    return copy.deepcopy(value)


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in {path}: {exc}") from exc


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json_text(value))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def bullets(values: Iterable[Any], empty: str = "- None") -> str:
    items = list(values)
    if not items:
        return empty
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("summary") or item.get("what") or item)
        else:
            text = str(item)
        lines.append(f"- {text}")
    return "\n".join(lines)


def numbered(values: Iterable[Any], empty: str = "1. None") -> str:
    items = list(values)
    if not items:
        return empty
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("action") or item.get("summary") or item)
        else:
            text = str(item)
        lines.append(f"{index}. {text}")
    return "\n".join(lines)


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )


def find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).expanduser().resolve()
    if not candidate.exists():
        return candidate
    probe = run(["git", "rev-parse", "--show-toplevel"], candidate)
    if probe.returncode == 0 and probe.stdout.strip():
        return Path(probe.stdout.strip()).resolve()
    return candidate


def file_tree(root: Path, exclude_top: set[str] | None = None) -> dict[str, bytes]:
    """Read every file under root into memory, optionally skipping top-level
    directories (used to keep immutable history out of transaction diffs)."""
    result: dict[str, bytes] = {}
    if not root.exists():
        return result
    excluded = exclude_top or set()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] in excluded:
                continue
            result[str(relative)] = path.read_bytes()
    return result
