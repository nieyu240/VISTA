"""Keep action/model cost metrics de-duplicated across trace streams."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from metrics import call_metrics


class MetricsTests(unittest.TestCase):
    def test_joins_action_timings_by_call_id_and_excludes_observation_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = root / "transport"
            reliability = root / "reliability"
            events = root / "artifacts"
            transport.mkdir()
            reliability.mkdir()
            events.mkdir()
            calls = [
                {
                    "event": "model_result",
                    "elapsed_seconds": 1.25,
                    "usage": {"prompt_tokens": 100, "completion_tokens": 25},
                    "returned_model": "test-model",
                },
                {
                    "event": "action_result",
                    "call_id": "action-1",
                    "tool": "act",
                    "elapsed_seconds": 0.8,
                },
                {
                    "event": "action_result",
                    "call_id": "state-1",
                    "tool": "report_state",
                    "elapsed_seconds": 0.01,
                },
            ]
            (transport / "calls.jsonl").write_text(
                "\n".join(json.dumps(row) for row in calls) + "\n",
                encoding="utf-8",
            )
            receipts = [
                {
                    "event": "tool_attempt",
                    "tool_call_id": "action-1",
                    "is_action": True,
                },
                {
                    "event": "action_receipt",
                    "tool_call_id": "action-1",
                    "elapsed_seconds": 0.75,
                },
            ]
            (reliability / "reliability.jsonl").write_text(
                "\n".join(json.dumps(row) for row in receipts) + "\n",
                encoding="utf-8",
            )
            (events / "events.jsonl").write_text(
                json.dumps(
                    {
                        "event": "tool_result",
                        "tool": "play",
                        "call_id": "action-1",
                        "elapsed_seconds": 0.9,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = call_metrics(root)

        self.assertEqual(metrics["model_calls"], 1)
        self.assertEqual(metrics["action_calls"], 1)
        self.assertEqual(metrics["action_seconds"], 0.8)
        self.assertEqual(metrics["prompt_tokens"], 100)
        self.assertEqual(metrics["completion_tokens"], 25)
        self.assertEqual(metrics["response_models"], ["test-model"])

    def test_missing_provider_usage_is_not_reported_as_zero_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = root / "transport"
            transport.mkdir()
            (transport / "calls.jsonl").write_text(
                json.dumps(
                    {
                        "event": "model_result",
                        "elapsed_seconds": 0.25,
                        "returned_model": "test-model",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = call_metrics(root)

        self.assertIsNone(metrics["prompt_tokens"])
        self.assertIsNone(metrics["completion_tokens"])
        self.assertIsNone(metrics["reasoning_tokens"])


if __name__ == "__main__":
    unittest.main()
