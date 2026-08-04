from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "state.py"


class Harness(unittest.TestCase):
    maxDiff = None
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str, input_json: dict | None = None, check: bool = True):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--root", str(self.project)],
            input=json.dumps(input_json) if input_json is not None else None,
            text=True,
            capture_output=True,
            cwd=self.project,
        )
        if check and proc.returncode != 0:
            self.fail(f"CLI failed: {proc.args}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc

    def json_out(self, proc):
        return json.loads(proc.stdout)

    def init(self, session: str = "s1") -> dict:
        return self.json_out(self.cli("init", "--session-id", session))

    def prep(self, session: str = "s1") -> dict:
        return self.json_out(self.cli("save-prep", "--session-id", session))

    def apply(self, delta: dict, session: str = "s1", dry_run: bool = False, check: bool = True):
        args = ["apply", "-", "--session-id", session]
        if dry_run:
            args.append("--dry-run")
        return self.cli(*args, input_json=delta, check=check)

    def read_data(self, name: str) -> dict:
        return json.loads((self.project / ".savestate" / "data" / f"{name}.json").read_text())

    def state_tree(self) -> dict[str, bytes]:
        root = self.project / ".savestate"
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        } if root.exists() else {}

    def seed_active_phase(self) -> None:
        self.init()
        delta = {
            "expected_revision": 1,
            "session_summary": "seed project",
            "handoff": {
                "last_session": ["Created the parser phase."],
                "resume_point": {
                    "action": "Run focused parser tests",
                    "location": "tests/test_parser.py",
                    "start_with": "pytest tests/test_parser.py -q",
                    "expected": "two known failures",
                    "verify": "pytest tests/test_parser.py -q",
                },
                "session_targets": ["Reproduce failures", "Correct parser"],
                "relevant_issues": ["Do not rewrite the tokenizer wholesale."],
                "open_threads": ["Nested delimiters remain unresolved."],
                "technical_summary": "Parser exists but is not validated.",
            },
            "vision_ops": [
                {
                    "op": "add",
                    "maturity": "confirmed",
                    "summary": "Build a reliable command platform",
                    "user_words": "I want a reliable command platform",
                    "user_approved": True,
                    "tags": ["automation"],
                }
            ],
            "goal_ops": [
                {
                    "op": "add_stage",
                    "title": "Build command processing",
                    "intent": "Commands can be parsed safely.",
                    "criteria": ["Parser validated"],
                    "status": "active",
                    "vision": ["VIS-001"],
                },
                {
                    "op": "add_phase",
                    "stage": "STG-001",
                    "title": "Validate the parser",
                    "intent": "Prove malformed input handling.",
                    "milestones": ["Focused tests pass"],
                    "status": "active",
                    "tags": ["parser"],
                    "vision": ["VIS-001"],
                },
            ],
            "git": {"commit": False},
        }
        self.apply(delta)


class SaveStateV3Tests(Harness):
    def test_init_creates_bundle(self):
        result = self.init()
        self.assertTrue(result["initialized"])
        for name in ("CURRENT.md", "VISION.md", "GOALS.md", "TECHNICAL_STATE.md", "LESSONS.md", "INDEX.md", "PENDING.md"):
            self.assertTrue((self.project / ".savestate" / name).exists(), name)
        self.assertEqual(self.read_data("current")["active_phase"], "")

    def test_load_is_read_only(self):
        self.init()
        before = self.state_tree()
        self.cli("load", "--lite")
        after = self.state_tree()
        self.assertEqual(before, after)

    def test_sparse_handoff_preserves_omitted_context(self):
        self.seed_active_phase()
        before = self.read_data("current")
        delta = {
            "expected_revision": 2,
            "session_summary": "recorded progress",
            "handoff": {"last_session": ["Reproduced the two failures."]},
            "accomplished": [{"what": "Reproduced failures", "how": "Ran focused tests", "evidence": "2 failures reproduced"}],
            "git": {"commit": False},
        }
        self.apply(delta)
        after = self.read_data("current")
        self.assertEqual(after["resume_point"], before["resume_point"])
        self.assertEqual(after["session_targets"], before["session_targets"])
        self.assertEqual(after["relevant_issues"], before["relevant_issues"])
        self.assertEqual(after["open_threads"], before["open_threads"])
        self.assertEqual(after["last_session"], ["Reproduced the two failures."])

    def test_unreached_targets_survive_until_explicitly_resolved(self):
        self.seed_active_phase()
        targets = self.read_data("current")["session_targets"]
        first, second = targets[0]["id"], targets[1]["id"]
        delta = {
            "expected_revision": 2,
            "session_summary": "finished first target",
            "handoff_ops": [{"op": "complete_target", "id": first, "result": "Failures reproduced"}],
            "git": {"commit": False},
        }
        self.apply(delta)
        after = self.read_data("current")["session_targets"]
        self.assertEqual(next(x for x in after if x["id"] == first)["status"], "completed")
        self.assertEqual(next(x for x in after if x["id"] == second)["status"], "open")
        current_md = (self.project / ".savestate" / "CURRENT.md").read_text()
        self.assertIn("Correct parser", current_md)
        self.assertNotIn("Reproduce failures", current_md)

    def test_dry_run_writes_nothing(self):
        self.seed_active_phase()
        before = self.state_tree()
        delta = {
            "expected_revision": 2,
            "session_summary": "dry run",
            "handoff": {"last_session": ["This must not persist."]},
            "git": {"commit": False},
        }
        result = self.json_out(self.apply(delta, dry_run=True))
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["nothing_written"])
        self.assertEqual(before, self.state_tree())

    def test_failed_delta_is_fully_transactional(self):
        self.init()
        before = self.state_tree()
        delta = {
            "expected_revision": 1,
            "session_summary": "invalid partial save",
            "goal_ops": [
                {"op": "add_stage", "title": "Valid stage", "status": "active"},
                {"op": "add_phase", "stage": "STG-999", "title": "Invalid phase", "status": "active"},
            ],
            "git": {"commit": False},
        }
        proc = self.apply(delta, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(before, self.state_tree())

    def test_stale_revision_is_rejected(self):
        self.init()
        self.cli("note", "--text", "A concurrent note")
        before = self.state_tree()
        proc = self.apply({"expected_revision": 1, "session_summary": "stale"}, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Stale delta", proc.stderr)
        self.assertEqual(before, self.state_tree())

    def test_same_session_does_not_archive(self):
        self.seed_active_phase()
        self.apply({"expected_revision": 2, "session_summary": "same session", "git": {"commit": False}})
        history = self.project / ".savestate" / "history"
        self.assertEqual([p for p in history.iterdir() if p.name != "journal"], [])

    def test_first_save_in_new_session_archives_once(self):
        self.seed_active_phase()
        self.apply({"expected_revision": 2, "session_summary": "new session", "git": {"commit": False}}, session="s2")
        history = [p for p in (self.project / ".savestate" / "history").iterdir() if p.is_dir() and p.name != "journal"]
        self.assertEqual(len(history), 1)
        manifest = json.loads((history[0] / "manifest.json").read_text())
        self.assertEqual(manifest["cycle_id"], "SSC-001")
        self.apply({"expected_revision": 3, "session_summary": "same new session", "git": {"commit": False}}, session="s2")
        history2 = [p for p in (self.project / ".savestate" / "history").iterdir() if p.is_dir() and p.name != "journal"]
        self.assertEqual(len(history2), 1)

    def test_closeout_archives_and_opens_new_cycle(self):
        self.seed_active_phase()
        result = self.json_out(self.cli("closeout", "--label", "end parser work", "--session-id", "s1"))
        self.assertEqual(result["closed_cycle"], "SSC-001")
        self.assertEqual(result["new_cycle"], "SSC-002")
        self.assertTrue((self.project / ".savestate" / result["archive"]).exists())

    def test_note_is_lightweight_and_resolved_by_id(self):
        self.init()
        first = self.json_out(self.cli("note", "--text", "First exact note"))["id"]
        second = self.json_out(self.cli("note", "--text", "Second exact note"))["id"]
        pending = self.read_data("pending")["items"]
        self.assertEqual([x["text"] for x in pending], ["First exact note", "Second exact note"])
        revision = json.loads((self.project / ".savestate" / "metadata.json").read_text())["revision"]
        self.apply({"expected_revision": revision, "session_summary": "resolve one", "resolve_notes": [first], "git": {"commit": False}})
        pending = self.read_data("pending")["items"]
        self.assertEqual(next(x for x in pending if x["id"] == first)["status"], "resolved")
        self.assertEqual(next(x for x in pending if x["id"] == second)["status"], "pending")
        self.assertIn("Second exact note", (self.project / ".savestate" / "PENDING.md").read_text())

    def test_confirmed_item_change_requires_approval(self):
        self.seed_active_phase()
        before = self.state_tree()
        proc = self.apply(
            {
                "expected_revision": 2,
                "session_summary": "silent vision rewrite",
                "vision_ops": [{"op": "update", "id": "VIS-001", "summary": "Different platform"}],
                "git": {"commit": False},
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(before, self.state_tree())

    def test_rejected_idea_requires_explicit_reactivation(self):
        self.init()
        self.apply(
            {
                "expected_revision": 1,
                "session_summary": "add uncertain idea",
                "vision_ops": [{"op": "add", "summary": "Direct messages", "maturity": "unrefined", "tags": ["messaging"]}],
                "git": {"commit": False},
            }
        )
        self.apply(
            {
                "expected_revision": 2,
                "session_summary": "reject idea",
                "vision_ops": [{"op": "reject", "id": "VIS-001", "reason": "out of scope"}],
                "git": {"commit": False},
            }
        )
        proc = self.apply(
            {
                "expected_revision": 3,
                "session_summary": "silent return",
                "vision_ops": [{"op": "reactivate", "id": "VIS-001", "reason": "might help"}],
                "git": {"commit": False},
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_vision_retrieval_by_tag(self):
        self.seed_active_phase()
        result = self.json_out(self.cli("vision", "--tags", "automation"))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["id"], "VIS-001")

    def test_future_stage_cannot_be_decomposed(self):
        self.seed_active_phase()
        self.apply(
            {
                "expected_revision": 2,
                "session_summary": "add future stage",
                "goal_ops": [{"op": "add_stage", "title": "Future deployment", "status": "planned"}],
                "git": {"commit": False},
            }
        )
        before = self.state_tree()
        proc = self.apply(
            {
                "expected_revision": 3,
                "session_summary": "overplan future work",
                "goal_ops": [{"op": "add_phase", "stage": "STG-002", "title": "Premature phase", "status": "planned"}],
                "git": {"commit": False},
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(before, self.state_tree())

    def test_planned_phase_gets_file_only_after_activation(self):
        self.init()
        self.apply(
            {
                "expected_revision": 1,
                "session_summary": "plan phase",
                "goal_ops": [
                    {"op": "add_stage", "title": "Active stage", "status": "active"},
                    {"op": "add_phase", "stage": "STG-001", "title": "Planned phase", "status": "planned"},
                ],
                "git": {"commit": False},
            }
        )
        phase_files = list((self.project / ".savestate" / "stages").rglob("PH-001*.md"))
        self.assertEqual(phase_files, [])
        self.apply(
            {
                "expected_revision": 2,
                "session_summary": "activate phase",
                "goal_ops": [{"op": "set_phase_status", "id": "PH-001", "status": "active"}],
                "git": {"commit": False},
            }
        )
        phase_files = list((self.project / ".savestate" / "stages").rglob("PH-001*.md"))
        self.assertEqual(len(phase_files), 1)

    def test_paused_phase_remains_visible_and_resumable(self):
        self.seed_active_phase()
        self.apply(
            {
                "expected_revision": 2,
                "session_summary": "pause parser",
                "goal_ops": [{"op": "set_phase_status", "id": "PH-001", "status": "paused", "reason": "auth is blocking", "resume_condition": "identity IDs stable"}],
                "git": {"commit": False},
            }
        )
        phase = self.json_out(self.cli("phase", "PH-001"))
        self.assertEqual(phase["status"], "paused")
        self.assertEqual(phase["resume_condition"], "identity IDs stable")
        self.assertEqual(len(list((self.project / ".savestate" / "stages").rglob("PH-001*.md"))), 1)

    def test_phase_completion_requires_evidence_or_unverified_flag(self):
        self.seed_active_phase()
        before = self.state_tree()
        proc = self.apply(
            {
                "expected_revision": 2,
                "session_summary": "unsupported completion",
                "goal_ops": [{"op": "complete_phase", "id": "PH-001", "outcome": "Done"}],
                "git": {"commit": False},
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(before, self.state_tree())

    def test_completed_phase_records_success_history(self):
        self.seed_active_phase()
        self.apply(
            {
                "expected_revision": 2,
                "session_summary": "complete parser",
                "goal_ops": [{"op": "complete_phase", "id": "PH-001", "outcome": "Parser validated", "evidence": ["40 tests pass"], "lesson": "Validate focused behavior before regression."}],
                "git": {"commit": False},
            }
        )
        phase = self.json_out(self.cli("phase", "PH-001"))
        self.assertEqual(phase["status"], "completed")
        self.assertEqual(phase["completion"]["evidence"], ["40 tests pass"])
        phase_md = list((self.project / ".savestate" / "stages").rglob("PH-001*.md"))[0].read_text()
        self.assertIn("Parser validated", phase_md)
        lessons = self.read_data("lessons")["items"]
        self.assertEqual(lessons[0]["kind"], "success")

    def test_doctor_detects_and_repairs_generated_markdown_drift(self):
        self.seed_active_phase()
        current = self.project / ".savestate" / "CURRENT.md"
        current.write_text(current.read_text() + "\nManual drift\n")
        result = self.json_out(self.cli("doctor", check=False))
        self.assertIn("CURRENT.md", result["drift"])
        repaired = self.json_out(self.cli("doctor", "--repair"))
        self.assertEqual(repaired["drift"], [])
        self.assertNotIn("Manual drift", current.read_text())

    def test_pointer_hook_is_compact_and_read_only(self):
        self.seed_active_phase()
        before = self.state_tree()
        payload = json.dumps({"cwd": str(self.project)})
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "--root", str(self.project)],
            input=payload,
            text=True,
            capture_output=True,
            cwd=self.project,
        )
        self.assertEqual(proc.returncode, 0)
        output = json.loads(proc.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("PH-001", context)
        self.assertLess(len(context), 1800)
        self.assertEqual(before, self.state_tree())

    def test_snapshot_contains_canonical_data_and_working_records(self):
        self.seed_active_phase()
        self.apply({"expected_revision": 2, "session_summary": "new session", "git": {"commit": False}}, session="s2")
        snapshot = next(p for p in (self.project / ".savestate" / "history").iterdir() if p.name != "journal")
        self.assertTrue((snapshot / "data" / "current.json").exists())
        self.assertTrue((snapshot / "CURRENT.md").exists())
        self.assertTrue(list((snapshot / "stages").rglob("PH-001*.md")))

    def test_works_outside_git(self):
        self.seed_active_phase()
        result = self.json_out(self.apply({"expected_revision": 2, "session_summary": "state commit test"}))
        self.assertEqual(result["git"]["reason"], "not-a-git-repository")

    def test_state_only_git_commit_preserves_unrelated_staged_code(self):
        subprocess.run(["git", "init"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.project, check=True)
        code = self.project / "app.py"
        code.write_text("print('base')\n")
        subprocess.run(["git", "add", "app.py"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.project, check=True, capture_output=True)
        self.init()
        subprocess.run(["git", "add", ".savestate"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "init state"], cwd=self.project, check=True, capture_output=True)
        code.write_text("print('changed')\n")
        subprocess.run(["git", "add", "app.py"], cwd=self.project, check=True)
        revision = json.loads((self.project / ".savestate" / "metadata.json").read_text())["revision"]
        result = self.json_out(self.apply({"expected_revision": revision, "session_summary": "save only state", "handoff": {"last_session": ["Updated state only."]}}))
        self.assertTrue(result["git"]["committed"])
        committed_files = subprocess.run(["git", "show", "--name-only", "--pretty=format:", "HEAD"], cwd=self.project, text=True, capture_output=True, check=True).stdout.splitlines()
        committed_files = [x for x in committed_files if x]
        self.assertTrue(committed_files)
        self.assertTrue(all(path.startswith(".savestate/") for path in committed_files))
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.project, text=True, capture_output=True, check=True).stdout.splitlines()
        self.assertIn("app.py", staged)

    def test_git_hooks_are_not_bypassed(self):
        subprocess.run(["git", "init"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.project, check=True)
        (self.project / "base.txt").write_text("base")
        subprocess.run(["git", "add", "base.txt"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.project, check=True, capture_output=True)
        self.init()
        hooks = self.project / ".git" / "hooks"
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        revision = json.loads((self.project / ".savestate" / "metadata.json").read_text())["revision"]
        result = self.json_out(self.apply({"expected_revision": revision, "session_summary": "hook should block"}))
        self.assertFalse(result["git"]["committed"])
        self.assertEqual(result["git"]["reason"], "git-commit-failed")


    def test_handoff_only_save_does_not_rewrite_unaffected_vision(self):
        self.seed_active_phase()
        vision_before = (self.project / ".savestate" / "VISION.md").read_bytes()
        result = self.json_out(self.apply({
            "expected_revision": 2,
            "session_summary": "handoff only",
            "handoff": {"last_session": ["Updated only the rolling handoff."]},
            "git": {"commit": False},
        }))
        vision_after = (self.project / ".savestate" / "VISION.md").read_bytes()
        self.assertEqual(vision_before, vision_after)
        self.assertNotIn("VISION.md", result["changed_files"])

    def test_stage_completion_requires_finished_phases_and_evidence(self):
        self.seed_active_phase()
        before = self.state_tree()
        proc = self.apply({
            "expected_revision": 2,
            "session_summary": "premature stage completion",
            "goal_ops": [{"op": "complete_stage", "id": "STG-001", "outcome": "Done", "evidence": ["claim"]}],
            "git": {"commit": False},
        }, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(before, self.state_tree())
        self.apply({
            "expected_revision": 2,
            "session_summary": "complete phase",
            "goal_ops": [{"op": "complete_phase", "id": "PH-001", "outcome": "Parser validated", "evidence": ["40 tests pass"]}],
            "git": {"commit": False},
        })
        self.apply({
            "expected_revision": 3,
            "session_summary": "complete stage",
            "goal_ops": [{"op": "complete_stage", "id": "STG-001", "outcome": "Command processing foundation complete", "evidence": ["All stage phases completed"]}],
            "git": {"commit": False},
        })
        stages = self.read_data("stages")["items"]
        self.assertEqual(stages[0]["status"], "completed")
        self.assertEqual(stages[0]["completion"]["evidence"], ["All stage phases completed"])

    def test_hard_budget_cap_rejects_transaction_before_write(self):
        self.init()
        config_path = self.project / ".savestate" / "config.json"
        config = json.loads(config_path.read_text())
        config["budgets"]["current_tokens"] = 20
        config["budgets"]["hard_multiplier"] = 1.0
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
        before = self.state_tree()
        proc = self.apply({
            "expected_revision": 1,
            "session_summary": "oversized current",
            "handoff": {"last_session": ["x" * 1000]},
            "git": {"commit": False},
        }, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("hard cap", proc.stderr)
        self.assertEqual(before, self.state_tree())

    def test_identifiers_continue_beyond_999(self):
        self.init()
        meta_path = self.project / ".savestate" / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["counters"]["VIS"] = 999
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        self.apply({
            "expected_revision": 1,
            "session_summary": "add thousandth vision item",
            "vision_ops": [{"op": "add", "summary": "Thousandth item", "maturity": "unrefined"}],
            "git": {"commit": False},
        })
        item = self.read_data("vision")["items"][0]
        self.assertEqual(item["id"], "VIS-1000")
        self.assertIn("VIS-1000", (self.project / ".savestate" / "VISION.md").read_text())

    def test_note_can_initialize_complete_bundle_without_opening_cycle(self):
        result = self.json_out(self.cli("note", "--text", "First idea"))
        self.assertTrue(result["noted"])
        for name in ("CURRENT.md", "VISION.md", "GOALS.md", "TECHNICAL_STATE.md", "LESSONS.md", "INDEX.md", "PENDING.md"):
            self.assertTrue((self.project / ".savestate" / name).exists())
        meta = json.loads((self.project / ".savestate" / "metadata.json").read_text())
        self.assertEqual(meta["active_cycle_id"], "")

    def test_worktree_root_detection_uses_git_rev_parse(self):
        main = self.project / "main"
        worktree = self.project / "worktree"
        main.mkdir()
        subprocess.run(["git", "init"], cwd=main, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=main, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=main, check=True)
        (main / "file.txt").write_text("base")
        subprocess.run(["git", "add", "file.txt"], cwd=main, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=main, check=True, capture_output=True)
        subprocess.run(["git", "worktree", "add", "-b", "test-worktree", str(worktree)], cwd=main, check=True, capture_output=True)
        proc = subprocess.run([sys.executable, str(SCRIPT), "init", "--root", str(worktree), "--session-id", "wt"], text=True, capture_output=True, cwd=worktree)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((worktree / ".savestate" / "CURRENT.md").exists())
        self.assertFalse((main / ".savestate").exists())



class SaveStateV31Tests(Harness):
    """Regression tests for the defects corrected in v3.1."""

    def seeded(self) -> None:
        self.init()
        self.apply(
            {
                "expected_revision": 1,
                "vision_ops": [
                    {
                        "op": "add",
                        "maturity": "confirmed",
                        "user_approved": True,
                        "summary": "Ranking system",
                        "tags": ["ranking"],
                    }
                ],
                "goal_ops": [
                    {
                        "op": "add_stage",
                        "title": "Build ranking",
                        "intent": "x",
                        "status": "active",
                        "vision": ["VIS-001"],
                    },
                    {
                        "op": "add_phase",
                        "stage": "STG-001",
                        "title": "Validate weighting",
                        "status": "active",
                    },
                ],
                "handoff": {
                    "session_targets": ["First target", "Second target"],
                    "relevant_issues": ["Do not normalize before weighting"],
                },
            }
        )

    def test_resolve_issue_with_wrong_text_is_rejected_not_silent(self):
        self.seeded()
        proc = self.apply(
            {
                "expected_revision": 2,
                "handoff_ops": [
                    {"op": "resolve_issue", "text": "Do not normalize before THE weighting"}
                ],
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("resolve_issue requires the existing text", proc.stderr)
        current = self.read_data("current")
        self.assertEqual(current["relevant_issues"], ["Do not normalize before weighting"])

    def test_blocked_targets_stay_visible_in_current_md(self):
        self.seeded()
        self.apply(
            {
                "expected_revision": 2,
                "handoff_ops": [
                    {"op": "block_target", "id": "ACT-001", "reason": "awaiting credentials"}
                ],
            }
        )
        text = (self.project / ".savestate" / "CURRENT.md").read_text()
        self.assertIn("Blocked targets", text)
        self.assertIn("First target (blocked: awaiting credentials)", text)
        lite = json.loads(self.cli("load", "--lite").stdout)
        self.assertEqual(len(lite["blocked_targets"]), 1)
        self.assertEqual(lite["blocked_targets"][0]["id"], "ACT-001")

    def test_add_target_op_does_not_pollute_stored_target(self):
        self.seeded()
        self.apply(
            {
                "expected_revision": 2,
                "handoff_ops": [{"op": "add_target", "text": "Run regressions"}],
            }
        )
        targets = self.read_data("current")["session_targets"]
        added = [t for t in targets if t["text"] == "Run regressions"][0]
        self.assertNotIn("op", added)
        self.assertEqual(set(added), {"id", "text", "status"})

    def test_completed_planned_phase_still_gets_durable_file(self):
        self.seeded()
        self.apply(
            {
                "expected_revision": 2,
                "goal_ops": [
                    {"op": "add_phase", "stage": "STG-001", "title": "Quick cleanup", "status": "planned"},
                    {
                        "op": "complete_phase",
                        "id": "PH-002",
                        "outcome": "done inline",
                        "evidence": ["one test passes"],
                    },
                ],
            }
        )
        files = list((self.project / ".savestate" / "stages").rglob("PH-002-*.md"))
        self.assertEqual(len(files), 1)

    def test_empty_session_id_surfaces_boundary_warning(self):
        self.seeded()
        prep = json.loads(self.cli("save-prep", "--session-id", "").stdout)
        self.assertTrue(any("boundary" in w for w in prep["session_warnings"]))
        result = self.json_out(
            self.cli(
                "apply",
                "-",
                "--session-id",
                "",
                input_json={
                    "expected_revision": 2,
                    "handoff_ops": [{"op": "append_last_session", "text": "more"}],
                },
            )
        )
        self.assertTrue(any("boundary=new" in w for w in result["warnings"]))

    def test_explicit_boundary_new_archives_without_session_id(self):
        self.seeded()
        result = self.json_out(
            self.cli(
                "apply",
                "-",
                "--session-id",
                "",
                input_json={
                    "expected_revision": 2,
                    "boundary": "new",
                    "handoff_ops": [{"op": "append_last_session", "text": "next day"}],
                },
            )
        )
        self.assertIsNotNone(result["archive_created"])

    def test_doctor_reports_orphaned_backup_and_restore_hint(self):
        self.seeded()
        backup = self.project / ".savestate.backup-deadbeef"
        shutil.copytree(self.project / ".savestate", backup)
        report = self.json_out(self.cli("doctor", check=False))
        self.assertTrue(any("Orphaned backup" in w for w in report["warnings"]))
        shutil.rmtree(self.project / ".savestate")
        report = self.json_out(self.cli("doctor", check=False))
        self.assertTrue(any("mv .savestate.backup-deadbeef .savestate" in e for e in report["errors"]))

    def test_transaction_diff_and_copy_exclude_history(self):
        self.seeded()
        # Create a boundary archive, then confirm a later save's changed_files
        # never mention history/ and that archived files survive untouched.
        self.apply({"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "a"}]}, session="s2")
        history = self.project / ".savestate" / "history"
        archived = list(history.rglob("CURRENT.md"))
        self.assertEqual(len(archived), 1)
        before_bytes = archived[0].read_bytes()
        result = self.json_out(
            self.apply({"expected_revision": 3, "handoff_ops": [{"op": "append_last_session", "text": "b"}]}, session="s2")
        )
        self.assertFalse(any(path.startswith("history/") for path in result["changed_files"]))
        self.assertEqual(archived[0].read_bytes(), before_bytes)

    def test_snapshot_excludes_events_by_default(self):
        self.seeded()
        self.apply({"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "a"}]}, session="s2")
        snapshot = next(p for p in (self.project / ".savestate" / "history").iterdir() if p.name != "journal")
        self.assertFalse((snapshot / "data" / "events.json").exists())
        manifest = json.loads((snapshot / "manifest.json").read_text())
        self.assertFalse(manifest["events_included"])
        self.assertTrue((snapshot / "data" / "current.json").exists())

    def test_init_extends_gitignore_idempotently(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        first = self.init()
        self.assertTrue(first["gitignore_updated"])
        content = (self.project / ".gitignore").read_text()
        self.assertIn(".savestate.lock", content)
        again = self.json_out(self.cli("init", check=False))
        self.assertFalse(again.get("gitignore_updated", False))
        self.assertEqual(content, (self.project / ".gitignore").read_text())

    def test_dead_pid_lock_is_reclaimed_immediately(self):
        self.seeded()
        lock = self.project / ".savestate.lock"
        lock.write_text("pid=999999 created=0\n")
        result = self.json_out(self.cli("note", "--text", "should succeed"))
        self.assertTrue(result["noted"])

    def test_generated_markdown_contains_no_em_dashes(self):
        self.seeded()
        for path in (self.project / ".savestate").rglob("*.md"):
            self.assertNotIn("\u2014", path.read_text(), f"em dash in {path}")

    def test_doctor_repair_without_drift_does_not_bump_revision(self):
        self.seeded()
        before = self.read_data("current")
        rev_before = json.loads((self.project / ".savestate" / "metadata.json").read_text())["revision"]
        report = self.json_out(self.cli("doctor", "--repair"))
        self.assertFalse(report["repaired"])
        rev_after = json.loads((self.project / ".savestate" / "metadata.json").read_text())["revision"]
        self.assertEqual(rev_before, rev_after)



class RedditRunFindingsTests(Harness):
    """Regression tests for the gaps found by the full project simulation."""

    def seeded(self):
        self.init()
        self.apply(
            {
                "expected_revision": 1,
                "goal_ops": [
                    {"op": "add_stage", "title": "Core model", "intent": "x", "status": "active"},
                    {"op": "add_stage", "title": "Infrastructure", "intent": "y", "status": "planned"},
                    {"op": "add_phase", "stage": "STG-001", "title": "Schema", "status": "active"},
                    {"op": "add_phase", "stage": "STG-001", "title": "Counters", "status": "planned"},
                ],
                "handoff": {
                    "relevant_issues": ["Do not trust client vote counts.\nAlways recount server side."]
                },
            }
        )

    def test_dangling_phase_supersede_is_rejected(self):
        self.seeded()
        proc = self.apply(
            {
                "expected_revision": 2,
                "goal_ops": [
                    {"op": "supersede_phase", "id": "PH-002", "by": "PH-999", "reason": "moves later"}
                ],
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not exist", proc.stderr)
        phases = self.read_data("phases")["items"]
        self.assertEqual([p for p in phases if p.get("superseded_by")], [])

    def test_dangling_vision_supersede_is_rejected(self):
        self.seeded()
        self.apply(
            {
                "expected_revision": 2,
                "vision_ops": [{"op": "add", "maturity": "developing", "summary": "Old idea"}],
            }
        )
        proc = self.apply(
            {
                "expected_revision": 3,
                "vision_ops": [
                    {"op": "supersede", "id": "VIS-001", "by": "VIS-999", "reason": "replaced"}
                ],
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not exist", proc.stderr)

    def test_move_phase_reparents_into_planned_stage_with_history(self):
        self.seeded()
        self.apply(
            {
                "expected_revision": 2,
                "goal_ops": [
                    {"op": "move_phase", "id": "PH-002", "to_stage": "STG-002", "reason": "belongs to infra"}
                ],
            }
        )
        phase = [p for p in self.read_data("phases")["items"] if p["id"] == "PH-002"][0]
        self.assertEqual(phase["stage"], "STG-002")
        self.assertTrue(any("Moved from STG-001" in h["text"] for h in phase["history"]))

    def test_move_phase_refuses_active_phase_and_retired_stage(self):
        self.seeded()
        proc = self.apply(
            {
                "expected_revision": 2,
                "goal_ops": [
                    {"op": "move_phase", "id": "PH-001", "to_stage": "STG-002", "reason": "nope"}
                ],
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("active phase", proc.stderr.lower())
        proc = self.apply(
            {
                "expected_revision": 2,
                "goal_ops": [
                    {"op": "move_phase", "id": "PH-002", "to_stage": "STG-404", "reason": "nope"}
                ],
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_multiline_issue_resolves_from_normalized_text(self):
        self.seeded()
        self.apply(
            {
                "expected_revision": 2,
                "handoff_ops": [
                    {
                        "op": "resolve_issue",
                        "text": "Do not trust client vote counts. Always recount server side.",
                    }
                ],
            }
        )
        self.assertEqual(self.read_data("current")["relevant_issues"], [])

    def test_ambiguous_normalized_resolution_is_rejected(self):
        self.seeded()
        self.apply(
            {
                "expected_revision": 2,
                "handoff_ops": [{"op": "add_issue", "text": "Do not trust client vote counts.\n\nAlways recount server side."}],
            }
        )
        proc = self.apply(
            {
                "expected_revision": 3,
                "handoff_ops": [
                    {
                        "op": "resolve_issue",
                        "text": "Do not trust client vote counts. Always recount server side.",
                    }
                ],
            },
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("several entries", proc.stderr)

    def test_reopened_phase_labels_prior_completion(self):
        self.seeded()
        self.apply(
            {
                "expected_revision": 2,
                "goal_ops": [
                    {"op": "complete_phase", "id": "PH-001", "outcome": "done", "evidence": ["tests pass"]},
                ],
            }
        )
        self.apply(
            {
                "expected_revision": 3,
                "goal_ops": [{"op": "set_phase_status", "id": "PH-001", "status": "active", "reason": "bug reopened"}],
            }
        )
        files = list((self.project / ".savestate" / "stages").rglob("PH-001-*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("Prior completion record (phase was reopened)", files[0].read_text())


if __name__ == "__main__":
    unittest.main()
