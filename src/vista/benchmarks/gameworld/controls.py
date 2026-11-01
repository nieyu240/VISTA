"""Observation-blind controls for validity audits; never construct an API client."""

from __future__ import annotations

import random

from vista.backends.session import BackendResult


class BlindPolicy:
    """Decisions depend only on the declared public action schema and RNG seed."""

    def __init__(self, schema, seed=42):
        self.rng = random.Random(seed)
        self.actions = [{"action": "wait", "duration": 0.05}]
        self.repeated = self.actions[0]
        for variant in schema["oneOf"]:
            props = variant["properties"]
            kind = props["action"]["const"]
            if kind == "press_key":
                self.actions.extend(
                    {"action": kind, "key": key, "duration": 0.2}
                    for key in props["key"]["enum"]
                )
                key = next(
                    (
                        key
                        for key in ("ArrowUp", "w", "Space")
                        if key in props["key"]["enum"]
                    ),
                    props["key"]["enum"][0],
                )
                self.repeated = {"action": kind, "key": key, "duration": 0.2}
            elif kind in {"click", "click_hold", "mouse_move"}:
                xmax, ymax = props["x"]["maximum"], props["y"]["maximum"]
                for x, y in ((xmax // 2, ymax // 2), (0, 0), (xmax, ymax)):
                    action = {"action": kind, "x": x, "y": y}
                    if kind != "click":
                        action["duration"] = 0.2
                    self.actions.append(action)
                if kind == "click_hold":
                    self.repeated = {
                        "action": kind,
                        "x": xmax // 2,
                        "y": ymax // 2,
                        "duration": 0.2,
                    }

    def next(self, mode):
        if mode == "repeat":
            return dict(self.repeated)
        if mode == "blind":
            return dict(self.rng.choice(self.actions))
        if mode == "wait":
            return dict(self.actions[0])
        raise ValueError(f"Unknown blind control: {mode}")


class ControlSession:
    """Use the normal runtime so budgets, evaluators and trace stay unchanged."""

    def __init__(self, runtime, policy, mode):
        self.runtime, self.policy, self.mode = runtime, policy, mode
        self.usage_reports = []
        self.index = 0

    def run(self, task_input):
        self.runtime.begin_turn(task_input)
        self.index += 1
        if self.mode == "text-only":
            return BackendResult(0, "Task completed. Success. WIN.", "no-model-control")
        action = self.policy.next(self.mode)
        call_id = f"control-{self.index}"
        self.runtime.artifacts.record(
            "control_decision",
            {"mode": self.mode, "action": action, "model_calls": 0},
        )
        self.runtime.execute("act", action, call_id)
        self.runtime.acknowledge(call_id)
        return BackendResult(0, "", "no-model-control", self.runtime.boundary)
