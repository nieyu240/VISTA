"""Unit tests for public evidence accounting; no evaluator state is supplied."""

import base64
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from vista.backends.api import ApiSettings
from vista.reliability import EvidenceHarness


def image(label="turn=0"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 40, 60)).save(stream, "PNG")
    return [
        {
            "data": base64.b64encode(stream.getvalue()).decode(),
            "mime_type": "image/png",
            "steer_text": label,
        }
    ]


class ReliabilityTests(unittest.TestCase):
    def make(self, **kwargs):
        settings = ApiSettings("https://example.invalid", "test", "model", **kwargs)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        harness = EvidenceHarness(settings, Path(directory.name))
        harness.begin_decision()
        visible = harness.present(image())
        self.assertIn("frame_id=", visible[0]["steer_text"])
        frame_id = next(iter(harness.current_frames))
        return harness, frame_id

    def claim(self, harness, frame_id, **changes):
        value = {
            "beliefs": [
                {
                    "field": "visible_count",
                    "value": "17",
                    "certainty": "observed",
                    "evidence": [
                        {
                            "episode_id": harness.episode,
                            "frame_id": frame_id,
                            "region": [0, 0, 1, 1],
                        }
                    ],
                }
            ],
            "subgoal": "collect nearby resource",
            "prediction": "the next action changes the visible count",
            "verification": {
                "verdict": "unknown",
                "reason": "no next frame",
                "evidence": [],
            },
            "progress": "unknown",
            "waiting": False,
            "completion_claim": False,
        }
        value.update(changes)
        return harness.report(value)

    def test_valid_claim_is_structurally_traced(self):
        harness, frame_id = self.make(reliability="ledger")
        text, accepted = self.claim(harness, frame_id)
        self.assertTrue(accepted)
        self.assertIn("structurally valid", text)
        summary = __import__(
            "vista.reliability", fromlist=["diagnostic_summary"]
        ).diagnostic_summary(harness.directory)
        self.assertEqual(summary["state_cards"], 1)
        self.assertEqual(summary["evidence_reference_coverage"], 1.0)

    def test_foreign_frame_is_rejected_and_cannot_score(self):
        harness, _ = self.make(reliability="verified")
        _, accepted = self.claim(harness, "frame-from-another-run")
        self.assertFalse(accepted)
        self.assertEqual(harness.ledger, None)

    def test_current_condition_rejects_historical_inspection(self):
        harness, _ = self.make(reliability="reliable", memory_mode="current")
        self.assertIn(
            "historical",
            harness.tool_guard("inspect", {"views": [{"event": 0, "frame": 0}]}),
        )


if __name__ == "__main__":
    unittest.main()
