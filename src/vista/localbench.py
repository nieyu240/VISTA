"""Local API-based VISTA deployment: real games, native tools, honest scores.

Run ``python -m vista.localbench --help`` in the dedicated Python 3.12 venv.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from vista.backends.api import (
    VISTA_HINT,
    ApiSession,
    ApiSettings,
    ChatClient,
    ProviderError,
    RunBudget,
    append_event,
    native_tools,
    trim_images,
    user_content,
)
from vista.reliability import EvidenceHarness, diagnostic_summary
from vista.task_protocol import TaskSpec, from_arc, from_gameworld

DATA = Path(
    os.getenv(
        "VISTA_BENCH_DATA", str(Path.home() / ".local/share/vista-benchmark")
    )
)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def source_identity():
    root = Path(__file__).resolve().parents[2]
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    )
    return {
        "base_commit": git.stdout.strip(),
        "source_sha256": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / "src/vista").rglob("*.py"))
        },
    }


def git_revision(path):
    """Return a pinned dataset revision without exposing repository internals."""
    path = Path(path)
    if not path.is_dir():
        return None
    revision = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    value = revision.stdout.strip()
    return value or None


def write_task_protocol(args, directory, result):
    """Persist the public task identity next to each result.

    The protocol intentionally contains only public task metadata. Private
    evaluator state and model credentials remain in their existing protected
    directories.
    """
    if args.profile == "arc3":
        spec = from_arc(
            game_id=result.get("game_id", args.game),
            seed=args.seed,
            max_steps=args.max_steps,
            max_model_calls=args.max_calls,
            max_tokens=getattr(args, "max_tokens", None),
            request_timeout_seconds=getattr(args, "timeout", None),
            task_timeout_seconds=getattr(args, "task_timeout", None),
            clock_mode=getattr(args, "clock_mode", "paused"),
        )
    elif args.profile == "gameworld":
        from vista.benchmarks.gameworld.upstream import load_upstream

        _, task = load_upstream(args.data / "GameWorld").task(args.game, args.task)
        spec = from_gameworld(
            game_id=args.game,
            task_id=args.task,
            objective=task.task_prompt,
            seed=args.seed,
            dataset_revision=git_revision(args.data / "GameWorld"),
            max_model_calls=args.max_calls,
            max_tool_calls=getattr(args, "max_tool_calls", args.max_calls * 3),
            max_tokens=getattr(args, "max_tokens", None),
            request_timeout_seconds=getattr(args, "timeout", None),
            task_timeout_seconds=getattr(args, "task_timeout", None),
            clock_mode=getattr(args, "clock_mode", "paused"),
        )
    else:
        spec = TaskSpec(
            suite="aigamestore",
            game_id=str(args.game),
            task_id=None,
            seed=args.seed,
            public_objective=f"Complete the public AI GameStore game {args.game}.",
            observation_mode="paused",
            clock_mode=getattr(args, "clock_mode", "paused"),
            max_decisions=args.max_steps,
            max_model_calls=args.max_calls,
            max_tool_calls=getattr(args, "max_tool_calls", args.max_calls * 3),
            max_tokens=getattr(args, "max_tokens", None),
            request_timeout_seconds=getattr(args, "timeout", None),
            task_timeout_seconds=getattr(args, "task_timeout", None),
            success_checker="AI GameStore native evaluator",
            failure_conditions=(
                "task_failure",
                "budget_exhausted",
                "infrastructure_error",
            ),
            dataset_revision=git_revision(args.data / "aigamestore"),
        )
    public_frames = sorted(
        (Path(directory) / "role-0/artifacts/context/visuals").glob("*.png")
    )
    if not public_frames:
        public_frames = sorted((Path(directory) / "frames").glob("*.png"))
    if public_frames:
        from dataclasses import replace

        spec = replace(
            spec,
            initial_observation_hash=hashlib.sha256(
                public_frames[0].read_bytes()
            ).hexdigest(),
        )
    spec.write(directory / "task_protocol.json")
    result["task_identity"] = spec.identity
    result["task_protocol"] = spec.protocol


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("doctor", "list", "run", "compare", "replay"))
    p.add_argument(
        "--profile", choices=("arc3", "gameworld", "aigamestore"), default="gameworld"
    )
    p.add_argument(
        "--reliability",
        choices=("legacy", "ledger", "verified", "reliable"),
        default="reliable",
        help="Public evidence/state ledger, verifier and bounded recovery preset",
    )
    p.add_argument(
        "--memory-mode",
        choices=("legacy", "current", "summary", "visual"),
        default="visual",
        help="Memory ablation: current observation, text summary or visual replay",
    )
    p.add_argument(
        "--inspection",
        choices=("off", "fixed", "agent"),
        default="agent",
        help="Inspection ablation; fixed is reserved for a scheduled controller",
    )
    p.add_argument("--inspection-budget", type=int, default=8)
    p.add_argument("--inspection-view-budget", type=int, default=24)
    p.add_argument("--stall-threshold", type=int, default=3)
    p.add_argument("--recovery-limit", type=int, default=6)
    p.add_argument("--data", type=Path, default=DATA)
    p.add_argument("--env-file", type=Path, action="append", default=[])
    p.add_argument("--model", default="deepseek-flash")
    p.add_argument("--game", default="01_2048")
    p.add_argument("--task", default="01_01")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--max-steps",
        type=int,
        default=100,
        help="ARC3 actions / GameStore decisions; GameWorld keeps its official 100 decisions",
    )
    p.add_argument("--max-calls", type=int, default=150)
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--timeout", type=float, default=90)
    p.add_argument("--task-timeout", type=float, default=1200)
    p.add_argument(
        "--clock-mode",
        choices=("paused", "wall", "token"),
        default="paused",
        help=(
            "Evaluation clock metadata. Current browser adapters implement paused; "
            "wall/token require a runtime that advances during inference."
        ),
    )
    p.add_argument("--image-history", type=int, default=8)
    p.add_argument("--strategy", choices=("baseline", "vista"), default="vista")
    p.add_argument(
        "--thinking",
        choices=("enabled", "disabled"),
        default="disabled",
        help="Explicit DeepSeek reasoning setting; recorded as a separate experiment",
    )
    p.add_argument("--output", type=Path)
    p.add_argument("--headed", action="store_true")
    p.add_argument(
        "--probe-model",
        action="store_true",
        help="One billed image + native-tool request",
    )
    return p


def settings(args):
    return ApiSettings.from_env(
        model=args.model,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
        max_calls=args.max_calls,
        task_timeout=args.task_timeout,
        image_history=args.image_history,
        strategy=args.strategy,
        seed=args.seed,
        thinking=args.thinking,
        reliability=args.reliability,
        memory_mode=args.memory_mode,
        inspection=args.inspection,
        inspection_budget=args.inspection_budget,
        inspection_view_budget=args.inspection_view_budget,
        stall_threshold=args.stall_threshold,
        recovery_limit=args.recovery_limit,
        clock_mode=args.clock_mode,
    )


def doctor(args):
    import importlib.metadata as md

    from PIL import Image, ImageDraw
    from playwright.sync_api import sync_playwright

    result = {
        "source": source_identity(),
        "dependencies": {
            n: md.version(n)
            for n in ("vista-arc3", "arc-agi", "playwright", "Pillow", "PyYAML")
        },
        "datasets": {},
        "model_requested": args.probe_model,
    }
    for name in ("GameWorld", "GameWorld-Games", "aigamestore"):
        root = args.data / name
        rev = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        result["datasets"][name] = {
            "exists": root.is_dir(),
            "revision": rev.stdout.strip(),
        }
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content("<canvas width=100 height=100></canvas>")
            result["chromium"] = {"ok": True, "version": browser.version}
            browser.close()
    except Exception as exc:
        result["chromium"] = {"ok": False, "error": str(exc)}
    if args.probe_model:
        if args.output is None:
            raise ValueError("--probe-model requires --output")
        s = settings(args)
        image = Image.new("RGB", (128, 64), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 63, 63), fill="red")
        draw.rectangle((64, 0, 127, 63), fill="blue")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        tool = {
            "name": "report_colors",
            "description": "Report the observed left and right colors.",
            "inputSchema": {
                "type": "object",
                "required": ["left", "right"],
                "properties": {"left": {"type": "string"}, "right": {"type": "string"}},
            },
        }
        client = ChatClient(s, args.output / "provider")
        try:
            message = client.complete(
                [
                    {
                        "role": "user",
                        "content": user_content(
                            "Inspect this image and call report_colors with the colors of its two halves.",
                            [
                                {
                                    "mime_type": "image/png",
                                    "data": base64.b64encode(
                                        stream.getvalue()
                                    ).decode(),
                                }
                            ],
                        ),
                    }
                ],
                native_tools([tool]),
            )
            calls = message.get("tool_calls") or []
            colors = json.loads(calls[0]["function"]["arguments"]) if calls else {}
            result["provider"] = {
                "base_url": s.base_url,
                "model": s.model,
                "vision_and_native_tools": (
                    colors.get("left", "").lower() == "red"
                    and colors.get("right", "").lower() == "blue"
                ),
                "observed_colors": colors,
                "usage": client.usage_reports,
            }
        except ProviderError as exc:
            result["provider"] = {"ok": False, "error": str(exc)}
    if args.output:
        save(args.output / "doctor.json", result)
    displayed = {k: v for k, v in result.items() if k != "source"}
    print(json.dumps(displayed, ensure_ascii=False, indent=2))
    return (
        0
        if result["chromium"]["ok"]
        and (
            not args.probe_model
            or result.get("provider", {}).get("vision_and_native_tools")
        )
        else 1
    )


def list_tasks(args):
    if args.profile == "gameworld":
        from vista.benchmarks.gameworld.upstream import load_upstream

        up = load_upstream(args.data / "GameWorld")
        return {
            "games": [
                {"game": game, "tasks": up.catalog.list_tasks(game)}
                for game in up.catalog.list_games()
            ]
        }
    if args.profile == "aigamestore":
        from vista.benchmarks.aigamestore.environment import load_public_game

        return {"games": [load_public_game(n).manifest() for n in range(1, 11)]}
    from arc_agi import Arcade, OperationMode

    arc = Arcade(operation_mode=OperationMode.ONLINE)
    return {"games": [e.model_dump(mode="json") for e in arc.get_environments()]}


def run_arc(args, s, directory):
    from arc_agi import Arcade, OperationMode
    from arcengine import GameState
    from jsonschema import Draft202012Validator

    from vista.benchmarks.arc3.codex.controller import GameController
    from vista.benchmarks.arc3.codex.tools import build_tools
    from vista.benchmarks.arc3.run import (
        finalize_scorecard,
        open_evaluation_scorecard,
        share_online_cookie_jar,
    )
    from vista.core.dispatcher import GameToolDispatcher, execution_from_response

    if not os.getenv("ARC_API_KEY"):
        raise ValueError("ARC_API_KEY missing; also load the existing ARC .env")
    directory.mkdir(parents=True, exist_ok=False)
    private, frames = directory / "private", directory / "frames"
    private.mkdir(mode=0o700)
    frames.mkdir()
    guide, working = directory / "GUIDE.md", directory / "WORKING.md"
    guide.write_text("No verified game rules yet.\n")
    working.write_text("")
    result = {
        "profile": "arc3",
        "protocol": "vista-api-online-v1",
        "game": args.game,
        "settings": s.public(),
        "source": source_identity(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    save(directory / "manifest.json", result)
    arc = Arcade(
        operation_mode=OperationMode.ONLINE, recordings_dir=str(private / "recordings")
    )
    env = controller = scorecard = None
    error, reason = None, None
    started = time.monotonic()
    client = ChatClient(s, private / "api")
    reliability = EvidenceHarness(s, directory / "reliability")
    try:
        catalog = arc.get_environments()
        matches = [
            e.game_id
            for e in catalog
            if e.game_id == args.game or e.game_id.startswith(args.game + "-")
        ]
        if len(matches) != 1:
            raise ValueError("Use a unique real game ID from list --profile arc3")
        result["game_id"] = matches[0]
        scorecard = open_evaluation_scorecard(arc)
        result["scorecard_id"] = scorecard
        env = arc.make(
            matches[0],
            scorecard_id=scorecard,
            save_recording=True,
            include_frame_data=True,
        )
        if env is None or env.observation_space is None:
            raise RuntimeError("environment_missing")
        share_online_cookie_jar(arc, env)
        controller = GameController(
            env=env,
            initial_observation=env.observation_space,
            private_dir=private,
            frames_dir=frames,
            max_steps=args.max_steps,
            max_invalid_retries=3,
            observation_mode="vision",
            render_scale=8,
            render_grid=True,
            reset_starts_fresh_session=False,
            guide_path=guide,
            working_path=working,
        )
        dispatcher = GameToolDispatcher(
            controller=controller, guide_path=guide, working_path=working
        )
        definitions = build_tools(
            512, include_compact_checkpoint=False, reset_requires_retry_state=False
        )
        model_definitions = reliability.definitions(definitions)
        validators = {
            d["name"]: Draft202012Validator(d["inputSchema"]) for d in definitions
        }
        initial = execution_from_response(
            controller._response(ok=True, action_applied=False)
        )
        system = "Complete this unfamiliar interactive game with as few game actions as possible. Use the registered tools and current visual."
        if s.strategy == "vista":
            system += "\n" + VISTA_HINT
        if reliability.instructions():
            system += (
                "\n\n# Reliability and evidence protocol\n" + reliability.instructions()
            )
        reliability.write_configuration(definitions, system)
        initial_images = reliability.present(initial.images, source="observation")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content(initial.text, initial_images)},
        ]
        while not controller.terminal:
            reliability.begin_decision()
            message = client.complete(
                trim_images(messages, s.image_history), native_tools(model_definitions)
            )
            reliability.model_response(
                client.calls,
                elapsed_seconds=client.last_elapsed,
                usage=client.usage_reports[-1] if client.usage_reports else None,
            )
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                reliability.text_only(message.get("content") or "")
                guidance = reliability.guidance()
                messages.append(
                    {
                        "role": "user",
                        "content": (guidance + "\n\n" if guidance else "")
                        + "Continue by calling a registered tool. Only the environment can confirm completion.",
                    }
                )
                continue
            views = []
            action_applied = False
            for call in calls:
                call_id, function = call.get("id", ""), call.get("function", {})
                name = function.get("name", "")
                reliability.bind_tool_call(call_id, name)
                is_action = name == "play"
                before = controller.step_index
                try:
                    arguments = json.loads(function.get("arguments", ""))
                except (ValueError, TypeError):
                    arguments = None
                valid = name in validators and validators[name].is_valid(arguments)
                tick = time.monotonic()
                if name == "report_state":
                    text, accepted = reliability.report(arguments)
                    execution = None
                else:
                    guard = reliability.tool_guard(name, arguments)
                    if guard:
                        text, accepted, execution = guard, False, None
                    elif not valid or action_applied or controller.terminal:
                        text = "Invalid or extra tool call; no action executed. Observe and use the tool schema."
                        accepted, execution = False, None
                    else:
                        execution = dispatcher.execute(name, arguments, call_id)
                        text, accepted = execution.text, execution.success
                        action_applied = controller.step_index > before
                reliability.tool_attempt(
                    name,
                    arguments,
                    call_id=call_id,
                    is_action=is_action,
                    accepted=accepted,
                    executed=is_action and controller.step_index > before,
                    environment_step_id=(
                        controller.step_index if is_action else None
                    ),
                    reason=None if accepted else text,
                )
                if name != "report_state" and execution is not None:
                    views.extend(execution.images)
                    if name == "play":
                        reliability.action(
                            name,
                            arguments,
                            text,
                            accepted,
                            time.monotonic() - tick,
                            call_id=call_id,
                            environment_step_id=controller.step_index,
                        )
                append_event(
                    directory / "events.jsonl",
                    {
                        "event": "tool_result",
                        "tool": name,
                        "call_id": call_id,
                        "arguments": arguments,
                        "text": text,
                        "step": controller.step_index,
                        "elapsed_seconds": time.monotonic() - tick,
                        "frames": [
                            str(p.relative_to(directory))
                            for p in controller._current_images
                        ],
                    },
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": text}
                )
            if views:
                views = reliability.present(views, source="action")
                messages.append(
                    {"role": "user", "content": user_content("Resulting views:", views)}
                )
            print(
                json.dumps(
                    {
                        "game": result["game_id"],
                        "step": controller.step_index,
                        "levels_completed": controller.obs.levels_completed,
                        "calls": client.calls,
                    }
                ),
                flush=True,
            )
        reason = controller.termination_reason
    except RunBudget:
        reason = "api_budget_exhausted"
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
        reason = (
            "infrastructure_error"
            if not isinstance(exc, KeyboardInterrupt)
            else "interrupted"
        )
    finally:
        if scorecard:
            closed, failure = finalize_scorecard(
                arc=arc, env=env, scorecard_id=scorecard, operation_mode="online"
            )
            result.update(closed)
            if failure and error is None:
                error = "scorecard_finalization_failed"
        obs = controller.obs if controller else None
        summary = result.get("scorecard_summary") or {}
        result.update(
            {
                "success": bool(obs and obs.state == GameState.WIN),
                "levels_completed": obs.levels_completed if obs else 0,
                "total_levels": obs.win_levels if obs else None,
                "steps": controller.step_index if controller else 0,
                "scored": bool(
                    controller and error is None and result.get("scorecard_closed")
                ),
                "official_score": summary.get("score") if error is None else None,
                "termination_reason": reason,
                "error": error,
                "model_calls": client.calls,
                "usage": client.usage_reports,
                "elapsed_seconds": time.monotonic() - started,
                "reliability": diagnostic_summary(directory),
            }
        )
        write_task_protocol(args, directory, result)
        save(directory / "result.json", result)
    return result


def run_one(args, directory):
    s = settings(args)
    if args.profile == "arc3":
        return run_arc(args, s, directory)

    def factory(runtime, path):
        return ApiSession(runtime, path, s)

    provenance = {
        "validation": "native-api-tools",
        **s.public(),
        "source": source_identity(),
    }
    if args.profile == "gameworld":
        from vista.benchmarks.gameworld.run import run_task
        from vista.benchmarks.gameworld.upstream import load_upstream

        result = run_task(
            load_upstream(args.data / "GameWorld"),
            games_root=args.data / "GameWorld-Games",
            game_id=args.game,
            task_id=args.task,
            directory=directory,
            backend="api",
            model=s.model,
            effort=s.thinking,
            backend_factory=factory,
            seed=args.seed,
            headless=not args.headed,
            backend_segments=s.max_calls,
            tool_calls=s.max_calls * 3,
            transport_provenance=provenance,
        )
        result["reliability"] = diagnostic_summary(directory)
        write_task_protocol(args, directory, result)
        save(directory / "result.json", result)
        return result
    from vista.benchmarks.aigamestore.environment import load_public_game
    from vista.benchmarks.aigamestore.run import run_task

    result = run_task(
        load_public_game(int(args.game)),
        directory=directory,
        backend="api",
        model=s.model,
        effort=s.thinking,
        backend_factory=factory,
        games_root=args.data / "aigamestore/games",
        seed=args.seed,
        headless=not args.headed,
        max_decisions=args.max_steps,
        backend_segments=s.max_calls,
        tool_calls=s.max_calls * 3,
        transport_provenance=provenance,
    )
    result["reliability"] = diagnostic_summary(directory)
    write_task_protocol(args, directory, result)
    save(directory / "result.json", result)
    return result


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.clock_mode != "paused":
        p.error(
            "Current local browser/ARC adapters support only --clock-mode paused; "
            "use a dedicated real-time adapter before collecting wall/token scores."
        )
    if (
        min(
            args.max_steps,
            args.max_calls,
            args.max_tokens,
            args.timeout,
            args.task_timeout,
            args.image_history,
        )
        <= 0
    ):
        p.error("Budgets, timeouts and image-history must be positive")
    if args.profile == "gameworld" and args.max_steps != 100:
        p.error(
            "GameWorld uses the upstream fixed 100-decision protocol; use --max-calls to cap API use"
        )
    for path in args.env_file:
        if not path.is_file():
            p.error(f"Environment file does not exist: {path}")
        load_dotenv(path, override=False)
    if args.command == "list":
        catalog = list_tasks(args)
        if args.output:
            save(args.output / "catalog.json", catalog)
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
        return 0
    if args.command == "doctor":
        return doctor(args)
    if args.output is None:
        p.error("--output is required")
    if args.command == "replay":
        from vista.replay import build_replay

        print(build_replay(args.output))
        return 0
    if args.output.exists():
        p.error(
            "Run/compare output must be a new directory; existing traces are preserved"
        )
    if args.command == "compare":
        results = []
        for strategy in ("baseline", "vista"):
            args.strategy = strategy
            result = run_one(args, args.output / strategy)
            from vista.replay import build_replay

            build_replay(args.output / strategy)
            results.append({"strategy": strategy, "result": result})
        # Preserve both runs, including failures; never report an incomplete mean.
        save(
            args.output / "comparison.json",
            {
                "profile": args.profile,
                "game": args.game,
                "task": args.task,
                "seed": args.seed,
                "runs": results,
                "note": "One paired trial is diagnostic, not evidence of general model superiority.",
            },
        )
        print(
            json.dumps(
                [{r["strategy"]: compact_result(r["result"])} for r in results],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if all(r["result"].get("scored") for r in results) else 1
    result = run_one(args, args.output)
    from vista.replay import build_replay

    build_replay(args.output)
    print(json.dumps(compact_result(result), ensure_ascii=False, indent=2))
    return 0 if result.get("scored") else 1


def compact_result(result):
    keys = (
        "game_id",
        "game",
        "task_id",
        "model",
        "success",
        "scored",
        "progress",
        "official_score",
        "levels_completed",
        "total_levels",
        "steps",
        "role_steps",
        "normalized_score",
        "raw_score",
        "decisions",
        "error",
        "termination_reason",
        "elapsed_seconds",
    )
    return {k: result[k] for k in keys if k in result}


if __name__ == "__main__":
    raise SystemExit(main())
