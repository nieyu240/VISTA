"""Versioned, hashable task protocol records used by every local run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ``clock_mode`` changes the identity schema, so this record format has a new
# version. The report reader still accepts existing v1 records.
PROTOCOL_VERSION = "task-protocol-v2"


@dataclass(frozen=True)
class TaskSpec:
    suite: str
    game_id: str
    task_id: str | None
    version: str = "v1"
    seed: int | None = None
    public_objective: str = ""
    observation_mode: str = "paused"
    clock_mode: str = "paused"
    max_decisions: int | None = None
    max_model_calls: int | None = None
    max_tool_calls: int | None = None
    max_tokens: int | None = None
    request_timeout_seconds: float | None = None
    task_timeout_seconds: float | None = None
    success_checker: str = "upstream-evaluator"
    failure_conditions: tuple[str, ...] = ()
    dataset_revision: str | None = None
    initial_state_hash: str | None = None
    initial_observation_hash: str | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.clock_mode not in {"paused", "wall", "token", "remote-official"}:
            raise ValueError(
                "clock_mode must be paused, wall, token or remote-official"
            )

    @property
    def protocol(self) -> str:
        return PROTOCOL_VERSION

    def canonical(self) -> dict[str, Any]:
        value = asdict(self)
        value["protocol"] = self.protocol
        value["failure_conditions"] = list(self.failure_conditions)
        value["labels"] = dict(sorted(self.labels.items()))
        return value

    @property
    def identity(self) -> str:
        identity_value = self.canonical()
        # Raster capture can differ by browser timing even when the public task
        # and seed are identical. Keep that capture as provenance, but do not
        # make it change the comparable task identity.
        identity_value.pop("initial_observation_hash", None)
        payload = json.dumps(identity_value, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def write(self, path: Path) -> None:
        value = self.canonical()
        value["task_identity"] = self.identity
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def from_gameworld(
    *,
    game_id: str,
    task_id: str,
    objective: str,
    seed: int | None,
    dataset_revision: str | None,
    max_model_calls: int | None = None,
    max_tool_calls: int | None = None,
    max_tokens: int | None = None,
    request_timeout_seconds: float | None = None,
    task_timeout_seconds: float | None = None,
    clock_mode: str = "paused",
    task_version: str = "v1",
) -> TaskSpec:
    return TaskSpec(
        suite="gameworld",
        game_id=game_id,
        task_id=task_id,
        version=task_version,
        seed=seed,
        public_objective=objective,
        observation_mode="paused",
        clock_mode=clock_mode,
        max_decisions=100,
        max_model_calls=max_model_calls,
        max_tool_calls=max_tool_calls,
        max_tokens=max_tokens,
        request_timeout_seconds=request_timeout_seconds,
        task_timeout_seconds=task_timeout_seconds,
        success_checker="GameWorld serialized-state evaluator",
        failure_conditions=("task_failure", "budget_exhausted", "infrastructure_error"),
        dataset_revision=dataset_revision,
    )


def from_arc(
    *,
    game_id: str,
    seed: int | None,
    max_steps: int,
    max_model_calls: int | None = None,
    max_tokens: int | None = None,
    request_timeout_seconds: float | None = None,
    task_timeout_seconds: float | None = None,
    clock_mode: str = "paused",
    task_version: str = "v1",
) -> TaskSpec:
    return TaskSpec(
        suite="arc3",
        game_id=game_id,
        task_id=None,
        version=task_version,
        seed=seed,
        public_objective="Discover the game rules and complete the official ARC environment.",
        observation_mode="remote-official",
        clock_mode=clock_mode,
        max_decisions=max_steps,
        max_model_calls=max_model_calls,
        max_tokens=max_tokens,
        request_timeout_seconds=request_timeout_seconds,
        task_timeout_seconds=task_timeout_seconds,
        success_checker="official ARC scorecard",
        failure_conditions=("not_finished", "budget_exhausted", "infrastructure_error"),
        labels={
            "seed_semantics": "client-config; remote environment seed not guaranteed"
        },
    )
