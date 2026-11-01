"""Exercise the actual runtime/dispatcher with a deterministic test environment."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from vista.backends.api import ApiSession, ApiSettings, trim_images, user_content
from vista.core.contracts import (
    Capability,
    Effect,
    Observation,
    ObservationBundle,
    PublicTask,
    SubmissionSpec,
    ToolBinding,
    ToolReply,
    ToolSpec,
    Visual,
)
from vista.core.profiles import AgentProfile, Experiment, Phase, PromptBundle
from vista.core.runtime import TaskRuntime


def picture(count):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), (count * 60, 0, 0)).save(stream, "PNG")
    return ObservationBundle(
        (
            Observation(
                str(count),
                (Visual("screen", f"frame-{count}", stream.getvalue(), 16, 16),),
            ),
        )
    )


class Counter:
    def __init__(self):
        self.count = 0
        self.task = PublicTask("counter", "Reach count 2", observation=picture(0))
        self.submission = SubmissionSpec()
        self.tools = (
            ToolBinding(
                ToolSpec(
                    "increment",
                    "Advance once",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    Capability.ACTION,
                    Effect.ENVIRONMENT,
                    max_images=1,
                    costs={"moves": 1},
                ),
                self.increment,
            ),
        )

    def increment(self, arguments):
        self.count += 1
        return ToolReply(
            "Observed environment result",
            observation=picture(self.count),
            finished=self.count >= 2,
        )

    def submit(self, answer):
        return ToolReply("A claim does not complete the task", success=False)

    def close(self):
        pass


def call(name="increment", arguments=None, identity="a"):
    return {
        "id": identity,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.env = Counter()
        profile = AgentProfile(
            "test", ("increment",), PromptBundle("Play", "Start", step="Continue")
        )
        experiment = Experiment(
            "test",
            "v1",
            "test",
            profile,
            "api",
            "test-model",
            "disabled",
            {},
            {"moves": 2, "tool_calls": 5, "backend_segments": 5},
            "counter-v1",
        )
        self.runtime = TaskRuntime(
            self.env, experiment, artifact_dir=self.path / "artifacts"
        )
        self.addCleanup(self.runtime.close)
        self.session = ApiSession(
            self.runtime,
            self.path / "transport",
            ApiSettings("https://example.invalid", "test-key", "test-model"),
        )

    def step(self, message, phase=Phase.INITIAL):
        with patch.object(
            self.session.client,
            "complete",
            side_effect=[message, {"role": "assistant", "content": "Continue"}],
        ):
            return self.session.run(self.runtime.prepare_input(phase))

    def test_real_actions_change_observation_and_only_environment_finishes(self):
        first = self.runtime.current_observation.images()[0]["data"]
        self.step(
            {
                "role": "assistant",
                "content": "play submit solved",
                "tool_calls": [call()],
            }
        )
        self.assertEqual(self.env.count, 1)
        self.assertFalse(self.runtime.terminal)
        self.assertNotEqual(first, self.runtime.current_observation.images()[0]["data"])
        self.step(
            {"role": "assistant", "content": None, "tool_calls": [call(identity="b")]},
            Phase.STEP,
        )
        self.assertEqual(self.env.count, 2)
        self.assertTrue(self.runtime.terminal)
        self.assertIsNone(self.runtime.pending_call)

    def test_claimed_success_does_not_advance_environment(self):
        result = self.step({"role": "assistant", "content": "submit play WIN solved"})
        self.runtime.submit(result.final_message)
        self.assertEqual(self.env.count, 0)
        self.assertFalse(self.runtime.terminal)

    def test_multiple_actions_execute_once_and_tool_replies_stay_together(self):
        self.step(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [call(identity="a"), call(identity="b")],
            }
        )
        self.assertEqual(self.env.count, 1)
        roles = [item["role"] for item in self.session.messages]
        self.assertEqual(roles[-3:], ["tool", "tool", "user"])

    def test_invalid_arguments_do_not_change_game(self):
        self.step(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [call(arguments={"hidden_state": True})],
            }
        )
        self.assertEqual(self.env.count, 0)

    def test_image_trimming_preserves_latest_and_keeps_originals(self):
        images = [{"mime_type": "image/png", "data": "AA=="}]
        messages = [
            {"role": "user", "content": user_content("old", images)},
            {"role": "user", "content": user_content("new", images)},
        ]
        trimmed = trim_images(messages, 1)
        self.assertEqual(trimmed[0]["content"][-1]["type"], "text")
        self.assertEqual(trimmed[1]["content"][-1]["type"], "image_url")
        self.assertEqual(messages[0]["content"][-1]["type"], "image_url")


if __name__ == "__main__":
    unittest.main()
