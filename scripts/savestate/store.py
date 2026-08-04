from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .constants import DATA_FILES, LOCK_FILENAME, STATE_DIRNAME
from .defaults import (
    default_config,
    default_current,
    default_events,
    default_index,
    default_lessons,
    default_metadata,
    default_pending,
    default_phases,
    default_stages,
    default_technical,
    default_vision,
)
from .util import read_json, write_json


class Store:
    def __init__(self, project_root: Path, state_dir: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.state_dir = (state_dir or (self.project_root / STATE_DIRNAME)).resolve()
        self.data_dir = self.state_dir / "data"
        self.stages_dir = self.state_dir / "stages"
        self.logs_dir = self.state_dir / "logs"
        self.history_dir = self.state_dir / "history"
        self.config_path = self.state_dir / "config.json"
        self.metadata_path = self.state_dir / "metadata.json"

    @property
    def exists(self) -> bool:
        return self.metadata_path.exists()

    def data_path(self, name: str) -> Path:
        return self.data_dir / name

    def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.stages_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

        defaults = {
            self.config_path: default_config(),
            self.metadata_path: default_metadata(),
            self.data_path("current.json"): default_current(),
            self.data_path("vision.json"): default_vision(),
            self.data_path("stages.json"): default_stages(),
            self.data_path("phases.json"): default_phases(),
            self.data_path("technical.json"): default_technical(),
            self.data_path("lessons.json"): default_lessons(),
            self.data_path("pending.json"): default_pending(),
            self.data_path("events.json"): default_events(),
            self.data_path("index.json"): default_index(),
        }
        for path, value in defaults.items():
            if not path.exists():
                write_json(path, value)

    def load_all(self) -> dict:
        if not self.exists:
            raise FileNotFoundError(f"SaveState is not initialized at {self.state_dir}")
        return {
            "config": read_json(self.config_path),
            "metadata": read_json(self.metadata_path),
            "current": read_json(self.data_path("current.json")),
            "vision": read_json(self.data_path("vision.json")),
            "stages": read_json(self.data_path("stages.json")),
            "phases": read_json(self.data_path("phases.json")),
            "technical": read_json(self.data_path("technical.json")),
            "lessons": read_json(self.data_path("lessons.json")),
            "pending": read_json(self.data_path("pending.json")),
            "events": read_json(self.data_path("events.json")),
            "index": read_json(self.data_path("index.json")),
        }

    def save_all(self, data: dict) -> None:
        write_json(self.config_path, data["config"])
        write_json(self.metadata_path, data["metadata"])
        for key in (
            "current",
            "vision",
            "stages",
            "phases",
            "technical",
            "lessons",
            "pending",
            "events",
            "index",
        ):
            write_json(self.data_path(f"{key}.json"), data[key])


class StateLock:
    def __init__(self, root: Path, stale_after_seconds: int = 3600) -> None:
        self.path = root / LOCK_FILENAME
        self.stale_after_seconds = stale_after_seconds
        self.acquired = False

    @staticmethod
    def _holder_pid(text: str) -> int | None:
        for token in text.split():
            if token.startswith("pid="):
                try:
                    return int(token[4:])
                except ValueError:
                    return None
        return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def acquire(self) -> None:
        if self.path.exists():
            detail = self.path.read_text(encoding="utf-8", errors="replace").strip()
            age = time.time() - self.path.stat().st_mtime
            holder = self._holder_pid(detail)
            holder_dead = holder is not None and not self._pid_alive(holder)
            if holder_dead or age > self.stale_after_seconds:
                # The owning process no longer exists, or the lock is old
                # enough to be presumed abandoned. Reclaim it.
                self.path.unlink(missing_ok=True)
            else:
                raise RuntimeError(
                    "SaveState is locked by another operation "
                    f"({detail or self.path}; about {int(age)}s old). "
                    "If no other Claude session is saving, delete the lock file and retry."
                )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(self.path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()} created={time.time()}\n")
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def __enter__(self) -> "StateLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _link_or_copy(source: str, destination: str) -> None:
    """Hardlink when possible; fall back to a normal copy. Used only for the
    immutable history tree, whose files are never rewritten in place."""
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


@contextmanager
def transaction_store(project_root: Path) -> Iterator[tuple[Store, Path]]:
    temp_root = Path(tempfile.mkdtemp(prefix=".savestate-txn-", dir=project_root))
    temp_state = temp_root / STATE_DIRNAME
    live_state = project_root / STATE_DIRNAME
    try:
        if live_state.exists():
            # Mutable files are copied so the transaction can rewrite them
            # freely. history/ is append-only and can dominate the bundle
            # size over time, so it is carried across by hardlink instead of
            # byte copy. Nothing in the save path rewrites history files in
            # place, which keeps the shared inodes safe.
            shutil.copytree(live_state, temp_state, ignore=shutil.ignore_patterns("history"))
            live_history = live_state / "history"
            if live_history.exists():
                shutil.copytree(live_history, temp_state / "history", copy_function=_link_or_copy)
        store = Store(project_root, temp_state)
        store.initialize()
        yield store, temp_root
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)


def commit_transaction(project_root: Path, temp_state: Path) -> Path | None:
    """Swap the transaction result into place.

    Returns the path of the preserved pre-commit backup (None when there was
    no prior live state). The caller must verify the new live state and then
    call discard_backup, or call restore_backup if verification fails. The
    backup is deliberately kept until then: a save is not finished when the
    bytes land, but when the landed bytes prove readable and valid.

    Failure handling is phase-aware. If the first rename (live to backup)
    fails, the live state was never touched and nothing is done. If the
    second rename (temp to live) fails, the preserved backup is moved back.
    The live state is never deleted on any path.
    """
    live_state = project_root / STATE_DIRNAME
    backup = project_root / f"{STATE_DIRNAME}.backup-{uuid.uuid4().hex}"
    moved = False
    try:
        if live_state.exists():
            os.replace(live_state, backup)
            moved = True
        os.replace(temp_state, live_state)
    except Exception:
        if moved and not live_state.exists():
            os.replace(backup, live_state)
        raise
    return backup if moved else None


def discard_backup(backup: Path | None) -> None:
    if backup is not None and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def restore_backup(project_root: Path, backup: Path | None) -> None:
    if backup is None or not backup.exists():
        return
    live_state = project_root / STATE_DIRNAME
    if live_state.exists():
        shutil.rmtree(live_state, ignore_errors=True)
    os.replace(backup, live_state)
