"""Pure host-side structural scorer for future Minecraft-style building tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

Block = tuple[int, int, int, str]


@dataclass(frozen=True)
class BuildingScore:
    target_blocks: int
    matched_blocks: int
    extra_blocks: int
    missing_blocks: int
    wrong_materials: int
    material_accuracy: float
    position_accuracy: float
    completion: float
    connected: bool | None
    entrance_reachable: bool | None
    constraint_violations: int

    def as_dict(self):
        return {
            "target_blocks": self.target_blocks,
            "matched_blocks": self.matched_blocks,
            "extra_blocks": self.extra_blocks,
            "missing_blocks": self.missing_blocks,
            "wrong_materials": self.wrong_materials,
            "material_accuracy": self.material_accuracy,
            "position_accuracy": self.position_accuracy,
            "completion": self.completion,
            "connected": self.connected,
            "entrance_reachable": self.entrance_reachable,
            "constraint_violations": self.constraint_violations,
        }


def _normalise(blocks: Iterable[Mapping | Block]) -> dict[tuple[int, int, int], str]:
    result = {}
    for block in blocks:
        if isinstance(block, Mapping):
            position = tuple(int(block[k]) for k in ("x", "y", "z"))
            material = str(block["material"])
        else:
            x, y, z, material = block
            position, material = (int(x), int(y), int(z)), str(material)
        result[position] = material
    return result


def score_building(
    target: Iterable[Mapping | Block],
    actual: Iterable[Mapping | Block],
    *,
    forbidden: Iterable[tuple[int, int, int]] = (),
    connected: bool | None = None,
    entrance_reachable: bool | None = None,
    require_connected: bool = False,
    require_entrance_reachable: bool = False,
) -> BuildingScore:
    """Compare exact world-state blocks; model text cannot affect this score.

    Translation/rotation tolerance is intentionally absent until a task manifest
    declares it. Forbidden positions are hard constraints and count once each.
    """
    expected, observed = _normalise(target), _normalise(actual)
    target_positions = set(expected)
    actual_positions = set(observed)
    matched = sum(p in observed and observed[p] == m for p, m in expected.items())
    wrong = sum(p in observed and observed[p] != m for p, m in expected.items())
    missing = len(target_positions - actual_positions)
    extra = len(actual_positions - target_positions)
    forbidden_set = {tuple(int(value) for value in position) for position in forbidden}
    violations = len(actual_positions & forbidden_set)
    violations += int(require_connected and connected is False)
    violations += int(require_entrance_reachable and entrance_reachable is False)
    material_total = len(target_positions)
    position_total = len(target_positions | actual_positions)
    return BuildingScore(
        target_blocks=len(expected),
        matched_blocks=matched,
        extra_blocks=extra,
        missing_blocks=missing,
        wrong_materials=wrong,
        material_accuracy=matched / material_total if material_total else 1.0,
        position_accuracy=(matched + wrong) / position_total if position_total else 1.0,
        completion=matched / material_total if material_total else 1.0,
        connected=connected,
        entrance_reachable=entrance_reachable,
        constraint_violations=violations,
    )


def load_building_score(target_path, actual_path, **kwargs):
    target = json.loads(Path(target_path).read_text(encoding="utf-8"))
    actual = json.loads(Path(actual_path).read_text(encoding="utf-8"))
    if isinstance(target, dict) and "target" in target:
        manifest = target
        target = manifest["target"]
        kwargs.setdefault("forbidden", manifest.get("forbidden", ()))
        constraints = manifest.get("constraints") or {}
        kwargs.setdefault("require_connected", constraints.get("connected", False))
        kwargs.setdefault(
            "require_entrance_reachable",
            constraints.get("entrance_reachable", False),
        )
    if isinstance(actual, dict):
        kwargs.setdefault("connected", actual.get("connected"))
        kwargs.setdefault("entrance_reachable", actual.get("entrance_reachable"))
    return score_building(
        target["blocks"] if isinstance(target, dict) else target,
        actual["blocks"] if isinstance(actual, dict) else actual,
        **kwargs,
    )
