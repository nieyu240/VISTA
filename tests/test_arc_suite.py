"""Check complete-catalog scoring and official scorecard identity."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_arc_suite import (  # noqa: E402
    official_game_score,
    prepare_infrastructure_retries,
    preserve_source_commit,
    summarize,
)


def result(game_id, score):
    return {
        "scored": True,
        "scorecard_closed": True,
        "scorecard_summary": {
            "score": score,
            "environments": [{"id": game_id, "score": score}],
        },
    }


class SuiteScoresTests(unittest.TestCase):
    def test_mismatched_or_open_scorecard_cannot_score(self):
        self.assertIsNone(official_game_score(result("other", 100), "target"))
        row = result("target", 100)
        row["scorecard_closed"] = False
        self.assertIsNone(official_game_score(row, "target"))

    def test_missing_game_withholds_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one").mkdir()
            (root / "one/result.json").write_text(json.dumps(result("one", 100)))
            score = summarize({"games": ["one", "two"], "configuration": {}}, root)
            self.assertFalse(score["complete"])
            self.assertIsNone(score["total_score"])

    def test_zero_score_stays_in_full_catalog_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for game, score in (("one", 100), ("two", 0)):
                (root / game).mkdir()
                (root / game / "result.json").write_text(
                    json.dumps(result(game, score))
                )
            score = summarize({"games": ["one", "two"], "configuration": {}}, root)
            self.assertTrue(score["complete"])
            self.assertEqual(score["total_score"], 50)

    @patch("run_arc_suite.subprocess.check_output", return_value="")
    @patch("run_arc_suite.subprocess.run", return_value=Mock(returncode=0))
    def test_resume_preserves_source_and_rejects_agent_or_budget_changes(
        self, diff, untracked
    ):
        previous = {
            "games": ["one"],
            "configuration": {"source_commit": "original", "max_calls": 150},
        }
        candidate = {
            "games": ["one"],
            "configuration": {"source_commit": "docs-update", "max_calls": 150},
        }
        preserve_source_commit(candidate, previous, Path("."))
        self.assertEqual(candidate, previous)
        candidate["configuration"]["max_calls"] = 151
        with self.assertRaises(ValueError):
            preserve_source_commit(candidate, previous, Path("."))
        diff.return_value = Mock(returncode=1)
        with self.assertRaises(ValueError):
            preserve_source_commit(candidate, previous, Path("."))

    def test_retry_retains_failures_and_does_not_repeat_valid_zero_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for game in ("failed", "zero"):
                (root / game).mkdir()
            failure = {
                "scored": False,
                "termination_reason": "infrastructure_error",
                "model_calls": 4,
            }
            (root / "failed/result.json").write_text(json.dumps(failure))
            (root / "zero/result.json").write_text(json.dumps(result("zero", 0)))
            attempts = prepare_infrastructure_retries(root, ["failed", "zero"])
            self.assertNotIn("zero", attempts)
            self.assertEqual(
                attempts["failed"], ["failed", "attempts/failed-attempt-2"]
            )
            replacement = root / attempts["failed"][-1]
            replacement.mkdir(parents=True)
            row = {**result("failed", 20), "model_calls": 3}
            (replacement / "result.json").write_text(json.dumps(row))
            score = summarize({"games": ["failed", "zero"], "configuration": {}}, root)
            self.assertEqual(score["total_score"], 10)
            self.assertEqual(score["model_calls_all_attempts"], 7)
            self.assertEqual(
                json.loads((root / "failed/result.json").read_text()), failure
            )
