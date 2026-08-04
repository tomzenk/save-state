from __future__ import annotations

"""Growth gauge: builds a multi-stage project in miniature and asserts that
the session-start load and the save-time overhead at the final stage stay
within a fixed band of their stage-one sizes. Every context leak found in
this project's history was invisible to example-based tests and obvious the
moment something measured growth; this test makes flatness a tested property
rather than a design intention."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from savestate import cli  # noqa: E402


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


def tokens(text: str) -> int:
    return (len(text) + 3) // 4


class GrowthGaugeTest(unittest.TestCase):
    STAGES = 6
    PHASES_PER_STAGE = 2

    def measure(self, root: str) -> dict:
        _, lite, _ = run_cli(["load", "--lite", "--root", root])
        current = (Path(root) / ".savestate" / "CURRENT.md").read_text()
        _, prep, _ = run_cli(["save-prep", "--session-id", "probe", "--root", root])
        index = (Path(root) / ".savestate" / "INDEX.md").read_text()
        return {
            "lite": tokens(lite),
            "current": tokens(current),
            "prep": tokens(prep),
            "index": tokens(index),
        }

    def test_load_costs_stay_flat_across_project_life(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = str(tmp)
            subprocess.run(["git", "init", "-q", root], check=True)
            code, out, err = run_cli(["init", "--root", root, "--session-id", "g0"])
            self.assertEqual(code, 0, err)
            rev, sid, first = 1, 0, None

            def apply(delta, session):
                nonlocal rev
                delta["expected_revision"] = rev
                code, out, err = run_cli(
                    ["apply", "-", "--root", root, "--session-id", session], json.dumps(delta)
                )
                self.assertEqual(code, 0, err)
                rev = json.loads(out)["revision"]

            for stg in range(1, self.STAGES + 1):
                sid += 1
                apply(
                    {"goal_ops": [{"op": "add_stage", "title": f"Stage {stg} outcome", "intent": "deliver", "status": "active"}]},
                    f"s{sid}",
                )
                stage_id = f"STG-{stg:03d}"
                for ph in range(self.PHASES_PER_STAGE):
                    sid += 1
                    apply(
                        {
                            "goal_ops": [{"op": "add_phase", "stage": stage_id, "title": f"S{stg}P{ph} work", "status": "active", "milestones": ["m1"]}],
                            "handoff": {
                                "last_session": [f"Started s{stg}p{ph}."],
                                "session_targets": [f"Task A s{stg}p{ph}", f"Task B s{stg}p{ph}"],
                                "resume_point": {"action": f"Continue s{stg}p{ph}"},
                            },
                        },
                        f"s{sid}",
                    )
                    code, out, _ = run_cli(["load", "--json", "--root", root])
                    pid = json.loads(out)["current"]["active_phase"]
                    sid += 1
                    apply(
                        {
                            "handoff_ops": [{"op": "append_last_session", "text": "Advanced the work."}],
                            "accomplished": [{"what": f"Subtask of {pid}", "how": "implemented", "evidence": "tests pass"}],
                            "failed": [{"attempt": "wrong order", "why": "ordering", "correction": "reorder"}],
                        },
                        f"s{sid}",
                    )
                    if not (stg == self.STAGES and ph == self.PHASES_PER_STAGE - 1):
                        sid += 1
                        code, out, _ = run_cli(["load", "--json", "--root", root])
                        targets = [
                            t["id"]
                            for t in json.loads(out)["current"]["session_targets"]
                            if t["status"] == "open"
                        ]
                        apply(
                            {
                                "handoff_ops": [{"op": "complete_target", "id": t, "result": "done"} for t in targets],
                                "goal_ops": [{"op": "complete_phase", "id": pid, "outcome": "met", "evidence": ["tests pass"]}],
                            },
                            f"s{sid}",
                        )
                if stg < self.STAGES:
                    sid += 1
                    apply(
                        {"goal_ops": [{"op": "complete_stage", "id": stage_id, "outcome": "delivered", "evidence": ["phases done"]}]},
                        f"s{sid}",
                    )
                if stg == 1:
                    first = self.measure(root)
            final = self.measure(root)

            # Relative band: the mature project must not load meaningfully
            # heavier than the young one.
            self.assertLessEqual(final["lite"], int(first["lite"] * 1.6) + 40, (first, final))
            self.assertLessEqual(final["current"], int(first["current"] * 1.6) + 60, (first, final))
            self.assertLessEqual(final["prep"], int(first["prep"] * 1.6) + 80, (first, final))

            # Absolute ceilings independent of the band.
            self.assertLess(final["lite"], 450, final)
            self.assertLess(final["current"], 1200, final)
            self.assertLess(final["prep"], 1400, final)
            self.assertLess(final["index"], 500, final)


if __name__ == "__main__":
    unittest.main()
