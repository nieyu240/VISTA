"""Tests for the public, host-side building score."""

import json
import tempfile
import unittest
from pathlib import Path

from vista.benchmarks.gameworld.building import load_building_score, score_building

TARGET = [
    {"x": 0, "y": 0, "z": 0, "material": "stone"},
    {"x": 1, "y": 0, "z": 0, "material": "stone"},
    {"x": 0, "y": 1, "z": 0, "material": "wood"},
]


class BuildingScoreTests(unittest.TestCase):
    def test_exact_match_is_complete(self):
        score = score_building(TARGET, TARGET, connected=True, entrance_reachable=True)
        self.assertEqual(score.completion, 1.0)
        self.assertEqual(score.matched_blocks, 3)
        self.assertEqual(score.extra_blocks, 0)
        self.assertTrue(score.connected)

    def test_missing_wrong_extra_and_forbidden_are_separate(self):
        actual = [
            {"x": 0, "y": 0, "z": 0, "material": "dirt"},
            {"x": 2, "y": 0, "z": 0, "material": "stone"},
        ]
        score = score_building(TARGET, actual, forbidden=[(2, 0, 0)])
        self.assertEqual(score.matched_blocks, 0)
        self.assertEqual(score.wrong_materials, 1)
        self.assertEqual(score.missing_blocks, 2)
        self.assertEqual(score.extra_blocks, 1)
        self.assertEqual(score.constraint_violations, 1)

    def test_manifest_constraints_require_actual_measurements(self):
        score = score_building(
            TARGET,
            TARGET,
            connected=False,
            entrance_reachable=False,
            require_connected=True,
            require_entrance_reachable=True,
        )
        self.assertEqual(score.constraint_violations, 2)

    def test_empty_target_is_defined(self):
        score = score_building([], [])
        self.assertEqual(score.completion, 1.0)
        self.assertEqual(score.material_accuracy, 1.0)
        self.assertEqual(score.position_accuracy, 1.0)

    def test_json_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            actual = root / "actual.json"
            target.write_text(
                json.dumps({"target": {"blocks": TARGET}, "forbidden": []}),
                encoding="utf-8",
            )
            actual.write_text(json.dumps({"blocks": TARGET}), encoding="utf-8")
            self.assertEqual(load_building_score(target, actual).completion, 1.0)


if __name__ == "__main__":
    unittest.main()
