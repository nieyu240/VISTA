#!/usr/bin/env python3
"""Run a declared task/seed/config matrix without hiding failures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call the model; otherwise emit the plan only",
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--env-file", action="append", default=[])
    args = parser.parse_args()
    args.matrix = args.matrix.resolve()
    args.output = args.output.resolve()
    args.python = str(Path(args.python).expanduser().resolve())
    args.env_file = [str(Path(path).expanduser().resolve()) for path in args.env_file]
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    jobs = []
    for task in matrix["tasks"]:
        for seed in matrix["seeds"]:
            for condition in matrix["conditions"]:
                run = f"{task['game']}-{task.get('task', 'task')}-seed{seed}-{condition['name']}"
                jobs.append(
                    {
                        "run": run,
                        "task": task,
                        "seed": seed,
                        "condition": condition,
                        "output": str(args.output / run),
                        "status": "pending",
                    }
                )
    existing_entries = (
        {item.name for item in args.output.iterdir()} if args.output.exists() else set()
    )
    existing_plan = args.output / "matrix.json"
    previous = None
    if existing_plan.is_file():
        previous = json.loads(existing_plan.read_text(encoding="utf-8"))
        if previous.get("matrix_definition") not in (None, matrix):
            parser.error(
                "Existing matrix definition differs; choose a new output directory"
            )
        if [job.get("run") for job in previous.get("jobs", [])] != [
            job["run"] for job in jobs
        ]:
            parser.error("Existing matrix plan differs; choose a new output directory")
    known_runs = {job["run"] for job in jobs}
    allowed_entries = {"matrix.json"}
    for item in existing_entries:
        if item in known_runs:
            allowed_entries.add(item)
            continue
        if any(
            re.fullmatch(
                re.escape(name) + r"-attempt-(?:[2-9]|[1-9][0-9]+)", item
            )
            for name in known_runs
        ):
            allowed_entries.add(item)
    if existing_entries - allowed_entries:
        parser.error(
            "Output directory contains files outside this matrix; existing runs are preserved"
        )
    if previous:
        jobs = previous["jobs"]
        for job in jobs:
            job.setdefault("status", "pending")
    args.output.mkdir(parents=True, exist_ok=True)
    plan = {
        "protocol": "matrix-v1",
        "created_at": (
            previous.get("created_at")
            if previous
            else datetime.now(timezone.utc).isoformat()
        ),
        "matrix_definition": matrix,
        "matrix_sha256": hashlib.sha256(
            json.dumps(matrix, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "executed": bool(args.execute or (previous or {}).get("executed")),
        "jobs": jobs,
    }
    plan_path = args.output / "matrix.json"

    def save_plan():
        temporary = plan_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(plan_path)

    save_plan()
    if not args.execute:
        print(
            json.dumps(
                {
                    "jobs": len(jobs),
                    "plan": str(args.output / "matrix.json"),
                    "executed": False,
                },
                ensure_ascii=False,
            )
        )
        return 0
    failures = 0
    wsl_root = Path(__file__).resolve().parents[1]
    if not Path(args.python).exists():
        parser.error(
            "--python must resolve to the dedicated Python executable inside WSL"
        )
    if any(not Path(path).is_file() for path in args.env_file):
        parser.error("An --env-file path does not exist in this WSL filesystem")
    project_dir = wsl_root
    if not (project_dir / "src/vista/localbench.py").is_file():
        parser.error("Could not resolve the benchmark VISTA source directory")
    for job in jobs:
        output_run = job.get("output_run", job["run"])
        existing_output = args.output.resolve() / output_run
        if job.get("status") in {"completed", "result-recovered"} and (
            existing_output / "result.json"
        ).is_file():
            continue
        condition = job["condition"]
        output = existing_output
        if output.exists():
            result_path = output / "result.json"
            if result_path.is_file():
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    job["status"] = "result-recovered"
                    job["scored"] = result.get("scored")
                    job["success"] = result.get("success")
                    job["progress"] = result.get("progress")
                    job["finished_at"] = datetime.now(timezone.utc).isoformat()
                    save_plan()
                    failures += result.get("scored") is not True
                    continue
                except (OSError, json.JSONDecodeError):
                    pass
            attempt = 2
            while True:
                candidate = f"{job['run']}-attempt-{attempt}"
                if not (args.output.resolve() / candidate).exists():
                    job.setdefault("attempt_history", []).append(
                        {
                            key: job.get(key)
                            for key in (
                                "output_run",
                                "status",
                                "started_at",
                                "finished_at",
                                "elapsed_seconds",
                                "returncode",
                            )
                            if job.get(key) is not None
                        }
                    )
                    output_run = candidate
                    job["output_run"] = output_run
                    output = args.output.resolve() / output_run
                    break
                attempt += 1
        else:
            job["output_run"] = output_run
        job["output"] = str(output)
        command_output = str(output)
        command = [
            args.python,
            "-m",
            "vista.localbench",
            "run",
            "--profile",
            matrix["profile"],
            "--game",
            job["task"]["game"],
            "--task",
            job["task"].get("task", ""),
            "--seed",
            str(job["seed"]),
            "--output",
            command_output,
            "--strategy",
            condition.get("strategy", "vista"),
            "--reliability",
            condition.get("reliability", "reliable"),
            "--memory-mode",
            condition.get("memory_mode", "visual"),
            "--inspection",
            condition.get("inspection", "agent"),
            "--max-calls",
            str(matrix.get("max_calls", 110)),
            "--max-tokens",
            str(matrix.get("max_tokens", 2048)),
            "--timeout",
            str(matrix.get("request_timeout", 90)),
            "--task-timeout",
            str(matrix.get("task_timeout", 900)),
            "--clock-mode",
            condition.get("clock_mode", matrix.get("clock_mode", "paused")),
        ]
        if matrix.get("model"):
            command.extend(["--model", matrix["model"]])
        for env_file in args.env_file:
            command.extend(["--env-file", env_file])
        environment = os.environ.copy()
        source_path = str(project_dir / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            value for value in (source_path, environment.get("PYTHONPATH", "")) if value
        )
        job["status"] = "running"
        job["output_run"] = output_run
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        started = datetime.now(timezone.utc)
        save_plan()
        try:
            completed = subprocess.run(
                command, cwd=project_dir, env=environment, check=False
            )
        except KeyboardInterrupt:
            job["status"] = "interrupted"
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            job["elapsed_seconds"] = (
                datetime.now(timezone.utc) - started
            ).total_seconds()
            save_plan()
            raise
        except OSError as exc:
            job["status"] = "launcher-error"
            job["launcher_error"] = type(exc).__name__
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            job["elapsed_seconds"] = (
                datetime.now(timezone.utc) - started
            ).total_seconds()
            failures += 1
            save_plan()
            continue
        job["returncode"] = completed.returncode
        job["status"] = "completed" if completed.returncode == 0 else "process-failed"
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        job["elapsed_seconds"] = (
            datetime.now(timezone.utc) - started
        ).total_seconds()
        result_path = output / "result.json"
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                job["scored"] = result.get("scored")
                job["success"] = result.get("success")
                job["progress"] = result.get("progress")
            except (OSError, json.JSONDecodeError):
                job["result_status"] = "unreadable"
        failures += completed.returncode != 0
        save_plan()
    plan["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_plan()
    failures = sum(
        job.get("status") in {"process-failed", "launcher-error"}
        or (
            job.get("status") == "result-recovered"
            and job.get("scored") is not True
        )
        for job in jobs
    )
    print(
        json.dumps(
            {"jobs": len(jobs), "failures": failures, "executed": True},
            ensure_ascii=False,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
