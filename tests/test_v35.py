"""v3.5 friction fixes found in real use: target IDs, revision lookup,
init + first save opening two cycles, and stale commit hashes."""

from __future__ import annotations

import json
import subprocess
import unittest

from test_state import Harness


def metadata(project):
    return json.loads((project / ".savestate" / "metadata.json").read_text())


def git(project, *args):
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, check=True)


class V35Fixes(Harness):
    def test_open_target_ids_are_bare_ids(self):
        self.init()
        self.apply({"expected_revision": self.prep()["expected_revision"],
                    "handoff_ops": [{"op": "add_target", "text": "write the reader"}]})
        prep = self.prep()
        self.assertEqual(prep["open_target_ids"], ["ACT-001"])
        self.assertEqual(prep["open_targets"][0]["text"], "write the reader")
        # the IDs from save-prep feed complete_target directly
        self.apply({"expected_revision": prep["expected_revision"],
                    "handoff_ops": [{"op": "complete_target", "id": prep["open_target_ids"][0], "result": "done"}]})
        self.assertEqual(self.prep()["open_target_ids"], [])

    def test_revision_command(self):
        out = self.json_out(self.cli("revision"))
        self.assertEqual(out, {"initialized": False, "revision": 0})
        self.init()
        before = self.json_out(self.cli("revision"))["revision"]
        self.cli("note", "--text", "an idea", "--session-id", "s1")
        self.assertEqual(self.json_out(self.cli("revision"))["revision"], before + 1)

    def test_stale_delta_names_the_cause(self):
        self.init()
        rev = self.prep()["expected_revision"]
        self.cli("note", "--text", "an idea", "--session-id", "s1")
        proc = self.apply({"expected_revision": rev, "session_summary": "x"}, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("A note", proc.stdout + proc.stderr)

    def test_first_save_after_init_reuses_the_fresh_cycle(self):
        self.init(session="")
        prep = self.prep(session="")
        self.assertEqual(prep["boundary"], "same")
        out = self.json_out(self.apply({"expected_revision": prep["expected_revision"], "boundary": "new",
                                        "session_summary": "first"}, session=""))
        meta = metadata(self.project)
        self.assertEqual(meta["active_cycle_id"], "SSC-001")
        self.assertTrue(any("had no saves yet" in w for w in out["warnings"]))
        # once a save has landed, boundary=new opens a new cycle as before
        self.apply({"expected_revision": self.prep(session="")["expected_revision"], "boundary": "new",
                    "session_summary": "second session"}, session="")
        self.assertEqual(metadata(self.project)["active_cycle_id"], "SSC-002")

    def test_doctor_flags_rewritten_commits(self):
        git(self.project, "init", "-q")
        git(self.project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "real")
        real = git(self.project, "rev-parse", "--short", "HEAD").stdout.strip()
        self.init()
        self.apply({"expected_revision": self.prep()["expected_revision"],
                    "goal_ops": [{"op": "add_stage", "title": "S", "intent": "i", "status": "active"},
                                 {"op": "add_phase", "stage": "STG-001", "title": "P", "intent": "i", "status": "active"}],
                    "accomplished": [{"what": "w", "how": "h", "evidence": "e", "phase": "PH-001",
                                      "commits": [real, "deadbee"]}],
                    "git": {"commit": False}})
        report = self.json_out(self.cli("doctor", check=False))
        flagged = [w for w in report["warnings"] if "Recorded commits not found" in w]
        self.assertEqual(len(flagged), 1)
        self.assertIn("deadbee (PH-001)", flagged[0])
        self.assertNotIn(real, flagged[0])


if __name__ == "__main__":
    unittest.main()
