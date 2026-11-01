"""Verify interrupted experiment matrices resume without overwriting traces."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_matrix


class MatrixResumeTests(unittest.TestCase):
    def test_interrupted_job_retries_to_a_new_attempt_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix_path = root / "matrix-definition.json"
            output = root / "matrix-output"
            matrix_path.write_text(
                json.dumps(
                    {
                        "profile": "gameworld",
                        "tasks": [{"game": "game", "task": "task"}],
                        "seeds": [11],
                        "conditions": [{"name": "baseline"}],
                    }
                ),
                encoding="utf-8",
            )
            args = [
                "run_matrix.py",
                "--matrix",
                str(matrix_path),
                "--output",
                str(output),
                "--execute",
                "--python",
                sys.executable,
            ]

            def interrupt_first_run(command, **_kwargs):
                run_dir = Path(command[command.index("--output") + 1])
                run_dir.mkdir(parents=True)
                (run_dir / "partial-trace.txt").write_text("keep", encoding="utf-8")
                raise KeyboardInterrupt

            with patch.object(sys, "argv", args), patch.object(
                run_matrix.subprocess, "run", side_effect=interrupt_first_run
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_matrix.main()

            original = output / "game-task-seed11-baseline"
            self.assertTrue((original / "partial-trace.txt").is_file())

            def complete_retry(command, **_kwargs):
                run_dir = Path(command[command.index("--output") + 1])
                run_dir.mkdir(parents=True)
                (run_dir / "result.json").write_text(
                    json.dumps({"scored": True, "success": True, "progress": 1.0}),
                    encoding="utf-8",
                )
                return type("Completed", (), {"returncode": 0})()

            with patch.object(sys, "argv", args), patch.object(
                run_matrix.subprocess, "run", side_effect=complete_retry
            ) as runner:
                self.assertEqual(run_matrix.main(), 0)
                self.assertEqual(runner.call_count, 1)

            plan = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
            job = plan["jobs"][0]
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["output_run"], "game-task-seed11-baseline-attempt-2")
            self.assertEqual(job["attempt_history"][0]["status"], "interrupted")
            self.assertTrue(
                (output / job["output_run"] / "result.json").is_file()
            )
            self.assertTrue((original / "partial-trace.txt").is_file())


if __name__ == "__main__":
    unittest.main()
