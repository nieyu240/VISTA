"""Small execution contract shared by model and audit controllers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from vista.core.runtime import TaskInput


@dataclass(frozen=True)
class BackendResult:
    returncode: int
    final_message: str
    session_id: str | None
    boundary: str | None = None


class BackendSession(Protocol):
    usage_reports: list[dict[str, Any] | None]

    def run(self, task_input: TaskInput) -> BackendResult: ...
