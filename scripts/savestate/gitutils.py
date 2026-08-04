from __future__ import annotations

from pathlib import Path

from .util import run


def is_repo(root: Path) -> bool:
    return run(["git", "rev-parse", "--is-inside-work-tree"], root).returncode == 0


def context(root: Path) -> dict:
    if not is_repo(root):
        return {
            "is_repo": False,
            "branch": "",
            "head": "",
            "worktree": "not-a-git-repository",
            "changed_files": [],
            "recent_commits": [],
        }
    branch = run(["git", "branch", "--show-current"], root).stdout.strip()
    head = run(["git", "rev-parse", "--short", "HEAD"], root).stdout.strip()
    status = run(["git", "status", "--porcelain=v1"], root).stdout.splitlines()
    changed: list[str] = []
    internal_prefixes = (".savestate/", ".savestate-txn-", ".savestate.backup-", ".savestate.lock")
    for line in status:
        if len(line) >= 4:
            path = line[3:].strip()
            if path.startswith(internal_prefixes):
                continue
            changed.append(path)
    recent = run(["git", "log", "-5", "--oneline"], root).stdout.splitlines()
    return {
        "is_repo": True,
        "branch": branch,
        "head": head,
        "worktree": "clean" if not status else "modified",
        "changed_files": changed,
        "recent_commits": recent,
    }


def state_only_commit(root: Path, state_dir_name: str, message: str) -> dict:
    if not is_repo(root):
        return {"committed": False, "reason": "not-a-git-repository"}

    changed = run(["git", "status", "--porcelain=v1", "--", state_dir_name], root)
    if not changed.stdout.strip():
        return {"committed": False, "reason": "no-state-changes"}

    # Stage state files so untracked files can be committed. Existing unrelated staged
    # changes remain staged; --only restricts the commit to the state path.
    add = run(["git", "add", "--", state_dir_name], root)
    if add.returncode != 0:
        return {
            "committed": False,
            "reason": "git-add-failed",
            "stderr": add.stderr.strip(),
        }

    commit = run(
        ["git", "commit", "--only", "-m", message, "--", state_dir_name],
        root,
    )
    if commit.returncode != 0:
        return {
            "committed": False,
            "reason": "git-commit-failed",
            "stdout": commit.stdout.strip(),
            "stderr": commit.stderr.strip(),
        }

    rev = run(["git", "rev-parse", "--short", "HEAD"], root).stdout.strip()
    return {"committed": True, "commit": rev, "message": message}


GITIGNORE_BLOCK = (
    "# SaveState runtime artifacts (safe to ignore; the state itself lives in .savestate/)",
    ".savestate.lock",
    ".savestate-txn-*/",
    ".savestate.backup-*/",
)


def ensure_gitignore(root: Path) -> bool:
    """Make sure transient SaveState artifacts are ignored. Returns True when
    the .gitignore was created or extended. Only acts inside a Git repository
    and never removes or reorders existing content."""
    if not is_repo(root):
        return False
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    missing = [line for line in GITIGNORE_BLOCK[1:] if line not in lines]
    if not missing:
        return False
    addition = ""
    if existing and not existing.endswith("\n"):
        addition += "\n"
    addition += "\n" + GITIGNORE_BLOCK[0] + "\n" + "\n".join(missing) + "\n"
    path.write_text(existing + addition, encoding="utf-8")
    return True
