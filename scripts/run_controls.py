#!/usr/bin/env python3
"""Execute real browser control audits with zero model calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

project_root = Path(__file__).resolve().parents[1]
source_root = project_root / "src"
sys.path.insert(0, str(source_root))

from vista.benchmarks.gameworld.computer_use import action_schema  # noqa: E402
from vista.benchmarks.gameworld.controls import (  # noqa: E402
    BlindPolicy,
    ControlSession,
)
from vista.benchmarks.gameworld.run import run_task  # noqa: E402
from vista.benchmarks.gameworld.upstream import load_upstream  # noqa: E402
from vista.localbench import save, write_task_protocol  # noqa: E402
from vista.replay import build_replay  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", default="18_minecraft-clone-glm")
    parser.add_argument("--task", default="18_01")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(
            os.getenv(
                "VISTA_BENCH_DATA",
                str(Path.home() / ".local/share/vista-benchmark"),
            )
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "reports/deployment/audits/gameworld-shortcuts-executed.json",
    )
    parser.add_argument(
        "--controllers",
        nargs="+",
        choices=("text-only", "wait", "repeat", "blind"),
        default=["text-only", "repeat", "blind"],
    )
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.audit_output = args.audit_output.resolve()
    if args.output.exists():
        parser.error("Use a new audit output directory; existing traces are preserved")
    upstream = load_upstream(args.data / "GameWorld")
    game, _ = upstream.task(args.game, args.task)
    schemas = [action_schema(game, role) for role in range(game.role_count)]
    rows = []
    args.output.mkdir(parents=True)
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    for mode in args.controllers:
        directory = args.output / mode

        def factory(runtime, path, _mode=mode):
            role = int(path.parent.name.removeprefix("role-"))
            return ControlSession(
                runtime, BlindPolicy(schemas[role], args.seed + role), _mode
            )

        result = run_task(
            upstream,
            games_root=args.data / "GameWorld-Games",
            game_id=args.game,
            task_id=args.task,
            directory=directory,
            backend="api",
            model=f"control/{mode}",
            effort="none",
            backend_factory=factory,
            seed=args.seed,
            backend_segments=110,
            tool_calls=110,
            transport_provenance={
                "validation": "no-model-control",
                "policy_information": "public action schema and RNG only; no observation or evaluator feedback",
                "controller": mode,
            },
        )
        spec_args = SimpleNamespace(
            profile="gameworld",
            game=args.game,
            task=args.task,
            seed=args.seed,
            max_steps=100,
            max_calls=0,
            max_tool_calls=110,
            max_tokens=None,
            timeout=None,
            task_timeout=None,
            data=args.data,
        )
        write_task_protocol(spec_args, directory, result)
        result.update({"control_type": mode, "model_calls": 0})
        save(directory / "result.json", result)
        build_replay(directory)
        rows.append(
            {
                key: result.get(key)
                for key in (
                    "control_type",
                    "scored",
                    "success",
                    "progress",
                    "error",
                    "role_steps",
                    "elapsed_seconds",
                )
            }
        )
        print(json.dumps(rows[-1]), flush=True)
        save(
            args.audit_output,
            {
                "protocol": "benchmark-audit-v1",
                "audit_type": "executed-blind-controls",
                "game": args.game,
                "task": args.task,
                "seed": args.seed,
                "model_calls": 0,
                "rows": rows,
                "runs_directory": str(
                    args.output.relative_to(Path(__file__).resolve().parents[1])
                )
                if args.output.is_relative_to(Path(__file__).resolve().parents[1])
                else str(args.output),
                "limitation": "Single-seed controls for this task only; control success alone is not proof of a reward bug.",
            },
        )
    return int(any(not row["scored"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
