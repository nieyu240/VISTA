"""Tests for reproducible public task identities."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vista.localbench import write_task_protocol
from vista.task_protocol import from_gameworld


class TaskProtocolTests(unittest.TestCase):
    def test_identity_is_stable_and_written(self):
        spec = from_gameworld(
            game_id="18_minecraft-clone-glm",
            task_id="18_01",
            objective="Collect the requested public resource.",
            seed=42,
            dataset_revision="dataset-commit",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task_protocol.json"
            spec.write(path)
            value = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(value["task_identity"], spec.identity)
        self.assertEqual(value["protocol"], "task-protocol-v2")
        self.assertEqual(value["seed"], 42)
        self.assertNotIn("private", value)

    def test_capture_hash_does_not_change_task_identity(self):
        first = from_gameworld(
            game_id="18_minecraft-clone-glm",
            task_id="18_01",
            objective="Collect the requested public resource.",
            seed=42,
            dataset_revision="dataset-commit",
        )
        second = __import__("dataclasses").replace(
            first, initial_observation_hash="different-frame"
        )
        self.assertEqual(first.identity, second.identity)

    def test_clock_mode_is_versioned_and_part_of_task_identity(self):
        first = from_gameworld(
            game_id="18_minecraft-clone-glm",
            task_id="18_01",
            objective="Collect the requested public resource.",
            seed=42,
            dataset_revision="dataset-commit",
        )
        realtime = replace(first, clock_mode="wall")
        self.assertEqual(first.protocol, "task-protocol-v2")
        self.assertNotEqual(first.identity, realtime.identity)

    def test_localbench_writes_gameworld_protocol(self):
        spec = SimpleNamespace(
            profile="gameworld",
            game="18_minecraft-clone-glm",
            task="18_01",
            seed=43,
            max_steps=100,
            max_calls=10,
            data=Path("/does/not/exist"),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = {}
            with patch(
                "vista.benchmarks.gameworld.upstream.load_upstream",
                return_value=SimpleNamespace(
                    task=lambda *_: (
                        None,
                        SimpleNamespace(task_prompt="Collect 20 dirt."),
                    )
                ),
            ):
                write_task_protocol(spec, Path(directory), result)
            value = json.loads(
                (Path(directory) / "task_protocol.json").read_text(encoding="utf-8")
            )
        self.assertEqual(value["game_id"], "18_minecraft-clone-glm")
        self.assertEqual(value["public_objective"], "Collect 20 dirt.")
        self.assertEqual(result["task_identity"], value["task_identity"])


if __name__ == "__main__":
    unittest.main()
