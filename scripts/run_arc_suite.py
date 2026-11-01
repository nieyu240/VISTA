#!/usr/bin/env python3
"""Retest a frozen ARC public catalog and aggregate its official game scores."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def official_game_score(result, game_id):
    """Use only a closed, single-game official scorecard with matching identity."""
    if not result.get("scored") or not result.get("scorecard_closed"):
        return None
    card = result.get("scorecard_summary") or {}
    environments = card.get("environments") or []
    if len(environments) != 1 or environments[0].get("id") != game_id:
        return None
    score = environments[0].get("score")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
        or not 0 <= score <= 100
    ):
        return None
    card_score = card.get("score")
    if not isinstance(card_score, (int, float)) or not math.isclose(
        score, card_score, abs_tol=1e-9
    ):
        return None
    return float(score)


def preserve_source_commit(plan, previous, project):
    """Allow documentation/runner commits while freezing the evaluated agent."""
    frozen = previous["configuration"]["source_commit"]
    runtime_paths = ["src/vista", "pyproject.toml"]
    changed = subprocess.run(
        ["git", "diff", "--quiet", frozen, "--", *runtime_paths],
        cwd=project,
        check=False,
    )
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *runtime_paths],
        cwd=project,
        text=True,
    )
    if changed.returncode or untracked.strip():
        raise ValueError(
            "Evaluated agent differs from the frozen source; use a new suite"
        )
    plan["configuration"]["source_commit"] = frozen
    if plan != previous:
        raise ValueError("Existing suite differs; use a new output directory")


def summarize(plan, root):
    attempts_path = root / "attempts.json"
    attempts = json.loads(attempts_path.read_text()) if attempts_path.is_file() else {}
    rows = []
    for game_id in plan["games"]:
        history = attempts.get(game_id, [game_id])
        selected = (root / history[-1]).resolve()
        if not selected.is_relative_to(root.resolve()):
            raise ValueError("Attempt directory escaped the suite")
        path = selected / "result.json"
        all_calls = sum(
            json.loads(p.read_text()).get("model_calls", 0)
            for relative in history
            if (p := root / relative / "result.json").is_file()
        )
        row = {
            "game_id": game_id,
            "status": "pending",
            "attempt_paths": history,
            "model_calls_all_attempts": all_calls,
        }
        if path.is_file():
            result = json.loads(path.read_text())
            score = official_game_score(result, game_id)
            calls = []
            for log in path.parent.rglob("calls.jsonl"):
                calls.extend(
                    json.loads(line)
                    for line in log.read_text().splitlines()
                    if line.strip()
                )
            usage = [
                c.get("usage") or {} for c in calls if c.get("event") == "model_result"
            ]
            row.update(
                status="scored" if score is not None else "unscored",
                score=score,
                success=result.get("success"),
                levels_completed=result.get("levels_completed"),
                total_levels=result.get("total_levels"),
                actions=result.get("steps"),
                model_calls=result.get("model_calls"),
                elapsed_seconds=result.get("elapsed_seconds"),
                prompt_tokens=sum(u["prompt_tokens"] for u in usage)
                if usage and all(isinstance(u.get("prompt_tokens"), int) for u in usage)
                else None,
                completion_tokens=sum(u["completion_tokens"] for u in usage)
                if usage
                and all(isinstance(u.get("completion_tokens"), int) for u in usage)
                else None,
                response_models=sorted(
                    {c["returned_model"] for c in calls if c.get("returned_model")}
                ),
                retries=sum(c.get("event") == "model_retry" for c in calls),
                transport_errors=sum(
                    c.get("event") == "model_transport_error" for c in calls
                ),
                termination_reason=result.get("termination_reason"),
                error=result.get("error"),
                scorecard_id=result.get("scorecard_id"),
            )
        rows.append(row)
    scored = [r for r in rows if r.get("score") is not None]
    complete = len(scored) == len(rows)
    return {
        "protocol": "arc-public-retest-v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": plan["configuration"],
        "planned": len(rows),
        "finished": sum(r["status"] != "pending" for r in rows),
        "scored": len(scored),
        "complete": complete,
        "total_score": sum(r["score"] for r in scored) / len(rows)
        if complete
        else None,
        "aggregation": "equal mean of official single-game scorecards; same game weighting as ARC SDK scorecard",
        "single_official_batch_scorecard": False,
        "games_completed": sum(r.get("success") is True for r in rows),
        "levels_completed": sum(r.get("levels_completed") or 0 for r in rows),
        "actions": sum(r.get("actions") or 0 for r in rows),
        "model_calls": sum(r.get("model_calls") or 0 for r in rows),
        "model_calls_all_attempts": sum(r["model_calls_all_attempts"] for r in rows),
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in rows),
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in rows),
        "games": rows,
    }


def prepare_infrastructure_retries(root, games):
    """Retry only unscored infrastructure failures, retaining every prior attempt."""
    path = root / "attempts.json"
    attempts = json.loads(path.read_text()) if path.is_file() else {}
    for game in games:
        history = attempts.get(game, [game])
        current = (root / history[-1]).resolve()
        if not current.is_relative_to(root.resolve()):
            raise ValueError("Attempt directory escaped the suite")
        result_path = current / "result.json"
        if not result_path.is_file():
            continue
        result = json.loads(result_path.read_text())
        if (
            result.get("scored")
            or result.get("termination_reason") != "infrastructure_error"
        ):
            continue
        index = len(history) + 1
        relative = f"attempts/{game}-attempt-{index}"
        while (root / relative).exists():
            index += 1
            relative = f"attempts/{game}-attempt-{index}"
        attempts[game] = [*history, relative]
    save(path, attempts)
    return attempts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--max-calls", type=int, default=150)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--task-timeout", type=int, default=1200)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--retry-infrastructure",
        action="store_true",
        help="Explicitly retry unscored infrastructure failures in new directories; adds model calls",
    )
    args = parser.parse_args()
    if (
        min(
            args.workers,
            args.max_steps,
            args.max_calls,
            args.max_tokens,
            args.task_timeout,
        )
        < 1
    ):
        parser.error("Workers and budgets must be positive")
    root = args.output.resolve()
    games = sorted(g["game_id"] for g in json.loads(args.catalog.read_text())["games"])
    if (
        not games
        or len(set(games)) != len(games)
        or any(not re.fullmatch(r"[a-z0-9]+-[a-z0-9]+", game) for game in games)
    ):
        parser.error("Catalog must contain distinct ARC game IDs")
    project = Path(__file__).resolve().parents[1]
    config = {
        "model": args.model,
        "max_steps": args.max_steps,
        "max_calls": args.max_calls,
        "max_tokens": args.max_tokens,
        "task_timeout": args.task_timeout,
        "request_timeout": 90,
        "image_history": 8,
        "strategy": "vista",
        "reliability": "reliable",
        "memory_mode": "visual",
        "inspection": "agent",
        "thinking": "disabled",
        "clock_mode": "paused",
        "workers": args.workers,
        "logical_call_limit": len(games) * args.max_calls,
        "catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip(),
    }
    plan = {"games": games, "configuration": config}
    plan_path = root / "suite.json"
    if plan_path.is_file():
        try:
            preserve_source_commit(plan, json.loads(plan_path.read_text()), project)
        except ValueError as error:
            parser.error(str(error))
    if root.exists() and not plan_path.exists():
        parser.error("Existing output is not a suite; use a new output directory")
    summary_path = root / "summary.json"
    previous_summary = (
        json.loads(summary_path.read_text()) if summary_path.is_file() else {}
    )
    previous_wall = previous_summary.get("wall_seconds", 0)
    save(plan_path, plan)
    summary = summarize(plan, root)
    summary["wall_seconds"] = previous_wall
    save(summary_path, summary)
    if not args.execute:
        print(
            json.dumps(
                {"planned": len(games), "executed": False, "configuration": config}
            ),
            flush=True,
        )
        return 0
    env_files = [p.resolve() for p in args.env_file]
    if any(not p.is_file() for p in env_files):
        parser.error("An environment file is missing")
    attempts_path = root / "attempts.json"
    attempts = json.loads(attempts_path.read_text()) if attempts_path.is_file() else {}
    if args.retry_infrastructure:
        attempts = prepare_infrastructure_retries(root, games)
    logs = root / "private/process-logs"
    logs.mkdir(parents=True, exist_ok=True)
    logs.chmod(0o700)
    fatal = threading.Event()

    def run(game_id):
        if fatal.is_set():
            return game_id, "halted_after_provider_failure"
        history = attempts.get(game_id, [game_id])
        output = (root / history[-1]).resolve()
        if not output.is_relative_to(root):
            raise ValueError("Attempt directory escaped the suite")
        if (output / "result.json").is_file():
            return game_id, "preserved"
        if output.exists():
            return game_id, "incomplete_directory_preserved"
        command = [
            sys.executable,
            "-m",
            "vista",
            "run",
            "--profile",
            "arc3",
            "--game",
            game_id,
            "--model",
            args.model,
            "--output",
            str(output),
            "--max-steps",
            str(args.max_steps),
            "--max-calls",
            str(args.max_calls),
            "--max-tokens",
            str(args.max_tokens),
            "--task-timeout",
            str(args.task_timeout),
            "--reliability",
            "reliable",
            "--memory-mode",
            "visual",
            "--inspection",
            "agent",
            "--strategy",
            config["strategy"],
            "--thinking",
            config["thinking"],
            "--clock-mode",
            config["clock_mode"],
            "--image-history",
            str(config["image_history"]),
            "--timeout",
            str(config["request_timeout"]),
        ]
        for path in env_files:
            command.extend(["--env-file", str(path)])
        with (logs / f"{game_id}-attempt-{len(history)}.log").open("w") as log:
            completed = subprocess.run(
                command, cwd=project, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        result_path = output / "result.json"
        if result_path.is_file():
            error = json.loads(result_path.read_text()).get("error") or ""
            if any(
                code in error
                for code in (
                    "insufficient_balance",
                    "invalid_api_key",
                    "model_permission_denied",
                )
            ):
                fatal.set()
        return game_id, completed.returncode

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, game) for game in games]
        for future in concurrent.futures.as_completed(futures):
            game, code = future.result()
            summary = summarize(plan, root)
            summary["wall_seconds"] = previous_wall + time.monotonic() - started
            summary["halted_after_provider_failure"] = fatal.is_set()
            save(root / "summary.json", summary)
            print(
                json.dumps(
                    {
                        "game": game,
                        "returncode": code,
                        "finished": summary["finished"],
                        "planned": len(games),
                        "scored": summary["scored"],
                        "total_score": summary["total_score"],
                    }
                ),
                flush=True,
            )
    return 0 if summary["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
