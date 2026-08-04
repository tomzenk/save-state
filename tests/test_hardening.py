from __future__ import annotations

"""Hardening suite: fault injection, the conservation auditor, post-commit
verification, the delta journal, and bounded conservation fuzzing.

These tests run the CLI in-process so they can inject faults and seed engine
bugs. Set SAVESTATE_FUZZ_STEPS to raise the fuzz depth for longer campaigns.
"""

import copy
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from savestate import cli, engine, store  # noqa: E402


def run_cli(args, stdin_text=None):
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    if stdin_text is not None:
        sys.stdin = io.StringIO(stdin_text)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
    except SystemExit as exc:
        code = int(exc.code or 0)
    finally:
        sys.stdin = old_stdin
    return code, out.getvalue(), err.getvalue()


def tree(root: Path):
    if not root.exists():
        return None
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


SEED_DELTA = {
    "expected_revision": 1,
    "goal_ops": [
        {"op": "add_stage", "title": "Build ranking", "intent": "x", "status": "active"},
        {"op": "add_phase", "stage": "STG-001", "title": "Validate weighting", "status": "active"},
    ],
    "handoff": {
        "session_targets": ["Reproduce failures", "Fix multiplier"],
        "relevant_issues": ["Do not normalize before weighting"],
        "resume_point": {"action": "Start with the failing tests"},
    },
}


class Harness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        code, out, err = run_cli(["init", "--root", str(self.project), "--session-id", "s1"])
        self.assertEqual(code, 0, err)

    def tearDown(self):
        self.temp.cleanup()

    def seed(self):
        code, out, err = run_cli(
            ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
            json.dumps(SEED_DELTA),
        )
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def state(self):
        return tree(self.project / ".savestate")

    def data(self, name):
        return json.loads((self.project / ".savestate" / "data" / f"{name}.json").read_text())


class CommitProtocolTests(Harness):
    def test_first_swap_failure_never_touches_live_state(self):
        """A transient failure moving live to backup must leave the live state
        byte-identical. This is the regression test for the catastrophic
        rollback bug found by crash injection: the old handler deleted the
        live state on exactly this path."""
        self.seed()
        before = self.state()
        original = os.replace
        calls = {"n": 0}

        def failing(src, dst):
            if ".savestate" in str(src) or ".savestate" in str(dst):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("injected failure on live->backup")
            return original(src, dst)

        store.os.replace = failing
        try:
            code, out, err = run_cli(
                ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
                json.dumps({"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "x"}]}),
            )
        finally:
            store.os.replace = original
        self.assertEqual(self.state(), before)
        self.assertFalse(list(self.project.glob(".savestate.backup-*")))

    def test_second_swap_failure_restores_backup(self):
        self.seed()
        before = self.state()
        original = os.replace
        calls = {"n": 0}

        def failing(src, dst):
            if ".savestate" in str(src) or ".savestate" in str(dst):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError("injected failure on temp->live")
            return original(src, dst)

        store.os.replace = failing
        try:
            run_cli(
                ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
                json.dumps({"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "x"}]}),
            )
        finally:
            store.os.replace = original
        self.assertEqual(self.state(), before)

    def test_every_write_point_failure_leaves_old_state(self):
        """Fail the Nth state-directory write for every N in an apply; the old
        state must survive byte-identical each time."""
        self.seed()
        before = self.state()
        original = Path.write_text
        n = 0
        while True:
            n += 1
            calls = {"n": 0}

            def failing(path_self, *a, _n=n, _calls=calls, **k):
                text = str(path_self)
                if ".savestate" in text:
                    _calls["n"] += 1
                    if _calls["n"] == _n:
                        raise OSError(f"injected write failure #{_n}")
                return original(path_self, *a, **k)

            Path.write_text = failing
            try:
                run_cli(
                    ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
                    json.dumps({"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "x"}]}),
                )
            finally:
                Path.write_text = original
            if calls["n"] < n:
                break
            self.assertEqual(self.state(), before, f"state changed after injected failure #{n}")
        self.assertGreater(n, 3, "expected several write points to have been probed")

    def test_post_commit_verification_failure_restores_previous_state(self):
        self.seed()
        before = self.state()
        original = cli.verify_live

        def corrupted(_store):
            return ["simulated post-commit corruption"]

        cli.verify_live = corrupted
        try:
            code, out, err = run_cli(
                ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
                json.dumps({"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "x"}]}),
            )
        finally:
            cli.verify_live = original
        self.assertNotEqual(code, 0)
        self.assertIn("previous state was restored", err)
        self.assertEqual(self.state(), before)
        self.assertFalse(list(self.project.glob(".savestate.backup-*")))


class ConservationAuditorTests(Harness):
    def test_auditor_blocks_a_seeded_handoff_erasure_bug(self):
        """Reintroduce the exact class of bug that motivated this project: a
        merge that silently resets the resume point. The independent auditor
        must reject the save and leave the state untouched, proving that a
        silent-forgetting defect now needs to exist in two implementations at
        once before it can reach disk."""
        self.seed()
        before = self.state()
        original = engine.merge_handoff

        def buggy(data, patch):
            original(data, patch)
            data["current"]["resume_point"] = {
                "action": "", "location": "", "start_with": "", "expected": "", "verify": ""
            }

        engine.merge_handoff = buggy
        try:
            code, out, err = run_cli(
                ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
                json.dumps({"expected_revision": 2, "handoff": {"last_session": ["only this"]}}),
            )
        finally:
            engine.merge_handoff = original
        self.assertNotEqual(code, 0)
        self.assertIn("Conservation audit failed", err)
        self.assertIn("resume point changed", err)
        self.assertEqual(self.state(), before)

    def test_auditor_blocks_seeded_target_loss(self):
        self.seed()
        before = self.state()
        original = engine.apply_handoff_ops

        def buggy(data, operations):
            original(data, operations)
            data["current"]["session_targets"] = data["current"]["session_targets"][1:]

        engine.apply_handoff_ops = buggy
        try:
            code, out, err = run_cli(
                ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
                json.dumps({"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "x"}]}),
            )
        finally:
            engine.apply_handoff_ops = original
        self.assertNotEqual(code, 0)
        self.assertIn("disappeared", err)
        self.assertEqual(self.state(), before)

    def test_auditor_is_silent_for_honest_saves(self):
        self.seed()
        code, out, err = run_cli(
            ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
            json.dumps(
                {
                    "expected_revision": 2,
                    "handoff_ops": [
                        {"op": "complete_target", "id": "ACT-001", "result": "reproduced"},
                        {"op": "resolve_issue", "text": "Do not normalize before weighting"},
                        {"op": "set_resume_point", "action": "Fix the multiplier"},
                    ],
                }
            ),
        )
        self.assertEqual(code, 0, err)


class JournalTests(Harness):
    def journal_files(self):
        journal = self.project / ".savestate" / "history" / "journal"
        return sorted(journal.glob("*.json")) if journal.exists() else []

    def test_every_apply_is_journaled_verbatim(self):
        self.seed()
        delta = {"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "journaled"}]}
        code, out, err = run_cli(
            ["apply", "-", "--root", str(self.project), "--session-id", "s1"], json.dumps(delta)
        )
        self.assertEqual(code, 0, err)
        files = self.journal_files()
        self.assertEqual(len(files), 2)
        record = json.loads(files[-1].read_text())
        self.assertEqual(record["delta"], delta)
        self.assertEqual(record["result_revision"], 3)
        self.assertTrue(record["before_hashes"])
        self.assertTrue(all(k.startswith("data/") for k in record["before_hashes"]))

    def test_dry_run_and_rejected_saves_leave_no_journal_entry(self):
        self.seed()
        count = len(self.journal_files())
        run_cli(
            ["apply", "-", "--root", str(self.project), "--session-id", "s1", "--dry-run"],
            json.dumps({"expected_revision": 2, "handoff_ops": [{"op": "append_last_session", "text": "x"}]}),
        )
        run_cli(
            ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
            json.dumps({"expected_revision": 2, "handoff_ops": [{"op": "complete_target", "id": "ACT-999"}]}),
        )
        self.assertEqual(len(self.journal_files()), count)

    def test_history_journal_listing(self):
        self.seed()
        code, out, err = run_cli(["history", "--root", str(self.project), "--journal"])
        self.assertEqual(code, 0, err)
        listing = json.loads(out)
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["journal"][0]["revision"], 2)


class GuidanceAndInputTests(Harness):
    def test_budget_hard_cap_error_includes_remediation(self):
        self.seed()
        code, out, err = run_cli(
            ["apply", "-", "--root", str(self.project), "--session-id", "s1"],
            json.dumps(
                {
                    "expected_revision": 2,
                    "handoff": {"last_session": ["y" * 400] * 40},
                }
            ),
        )
        self.assertNotEqual(code, 0)
        self.assertIn("Distill it in this same delta", err)

    def test_note_accepts_dash_leading_text_via_stdin(self):
        code, out, err = run_cli(["note", "--root", str(self.project)], "--- starts with dashes")
        self.assertEqual(code, 0, err)
        pending = (self.project / ".savestate" / "PENDING.md").read_text()
        self.assertIn("--- starts with dashes", pending)


class ConservationFuzzTests(Harness):
    def test_randomized_conservation(self):
        """Random deltas with an independent shadow model of what must
        survive; every fact the model tracks is re-verified after every save,
        and injected-invalid deltas must leave the state byte-identical."""
        steps = int(os.environ.get("SAVESTATE_FUZZ_STEPS", "60"))
        random.seed(1187)
        texts = ['plain', 'quote " x', 'multi\nline', '## inject', '---', 'émoji 🚀', 'x' * 120]
        shadow = {"open": {}, "issues": set(), "resume": "", "notes": {}}
        session, revision = "s1", 1
        self.seed()
        revision = 2
        cur = self.data("current")
        shadow["open"] = {t["id"]: t["text"] for t in cur["session_targets"] if t["status"] == "open"}
        shadow["issues"] = set(cur["relevant_issues"])
        shadow["resume"] = "Start with the failing tests"
        applies = 0
        for step in range(steps):
            keep = copy.deepcopy(shadow)
            ops, delta = [], {}
            for _ in range(random.randint(1, 3)):
                k = random.random()
                if k < 0.3:
                    ops.append({"op": "add_target", "text": random.choice(texts)})
                elif k < 0.45 and shadow["open"]:
                    tid = random.choice(list(shadow["open"]))
                    ops.append({"op": "complete_target", "id": tid, "result": "done"})
                    shadow["open"].pop(tid)
                elif k < 0.6:
                    t = f"issue {step}-{random.randint(0, 9)}"
                    ops.append({"op": "add_issue", "text": t})
                    shadow["issues"].add(t)
                elif k < 0.7 and shadow["issues"]:
                    t = random.choice(sorted(shadow["issues"]))
                    ops.append({"op": "resolve_issue", "text": t})
                    shadow["issues"].discard(t)
                elif k < 0.85:
                    a = f"resume {step}"
                    ops.append({"op": "set_resume_point", "action": a})
                    shadow["resume"] = a
                else:
                    ops.append({"op": "append_last_session", "text": random.choice(texts)})
            delta = {"expected_revision": revision, "handoff_ops": ops}
            if random.random() < 0.15:
                delta["handoff_ops"] = [*ops, {"op": "complete_target", "id": "ACT-99999"}]
                before = self.state()
                code, out, err = run_cli(
                    ["apply", "-", "--root", str(self.project), "--session-id", session],
                    json.dumps(delta),
                )
                self.assertNotEqual(code, 0)
                self.assertEqual(self.state(), before, f"step {step}: invalid delta changed state")
                shadow = keep
                continue
            code, out, err = run_cli(
                ["apply", "-", "--root", str(self.project), "--session-id", session],
                json.dumps(delta),
            )
            self.assertEqual(code, 0, f"step {step}: {err}")
            revision = json.loads(out)["revision"]
            applies += 1
            cur = self.data("current")
            live_open = {t["id"] for t in cur["session_targets"] if t["status"] == "open"}
            for tid in shadow["open"]:
                self.assertIn(tid, live_open, f"step {step}: open target {tid} vanished")
            shadow["open"] = {t["id"]: t["text"] for t in cur["session_targets"] if t["status"] == "open"}
            for t in shadow["issues"]:
                self.assertIn(t, cur["relevant_issues"], f"step {step}: issue lost")
            action = cur["resume_point"]
            action = action if isinstance(action, str) else action.get("action", "")
            self.assertEqual(action, shadow["resume"], f"step {step}: resume drifted")
        self.assertGreater(applies, steps // 2)
        code, out, err = run_cli(["doctor", "--root", str(self.project)])
        report = json.loads(out)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report.get("drift", []), [])


if __name__ == "__main__":
    unittest.main()
