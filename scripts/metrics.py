"""Shared run-level cost summaries for reports and the local dashboard."""

from __future__ import annotations

import json
from pathlib import Path


def percentile(values: list[float], quantile: float) -> float | None:
    """Return a linearly interpolated percentile, including for one sample."""
    if not values:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def call_metrics(run_directory: Path) -> dict:
    """Read transport/action timing logs without inspecting prompts or secrets."""
    model_results: list[dict] = []
    model_retries = 0
    transport_errors = 0
    call_action_timings: dict[str, float] = {}
    receipt_action_timings: dict[str, float] = {}
    event_action_timings: dict[str, float] = {}
    action_call_ids: set[str] = set()
    orphan_receipt_timings: list[float] = []
    fallback_event_play_timings: list[float] = []
    fallback_call_play_timings: list[float] = []
    non_action_tools = {
        "report_state",
        "inspect",
        "read_pixels",
        "history",
        "read_guide",
        "write_guide",
        "read_working",
        "write_working",
        "save_compact_checkpoint",
    }
    for log_path in run_directory.rglob("calls.jsonl"):
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("event")
            if kind == "model_result":
                model_results.append(event)
            elif kind == "model_retry":
                model_retries += 1
            elif kind == "model_transport_error":
                transport_errors += 1
            elif kind == "action_result":
                elapsed = event.get("elapsed_seconds")
                call_id = event.get("call_id")
                if (
                    isinstance(call_id, str)
                    and isinstance(elapsed, (int, float))
                    and elapsed >= 0
                    and event.get("tool") not in non_action_tools
                ):
                    call_action_timings[call_id] = float(elapsed)
                elif (
                    event.get("tool") == "play"
                    and isinstance(elapsed, (int, float))
                    and elapsed >= 0
                ):
                    fallback_call_play_timings.append(float(elapsed))

    for log_path in run_directory.rglob("reliability.jsonl"):
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            call_id = event.get("tool_call_id")
            if (
                event.get("event") == "tool_attempt"
                and event.get("is_action")
                and isinstance(call_id, str)
            ):
                action_call_ids.add(call_id)
            if event.get("event") != "action_receipt":
                continue
            elapsed = event.get("elapsed_seconds")
            if isinstance(call_id, str):
                action_call_ids.add(call_id)
                if isinstance(elapsed, (int, float)) and elapsed >= 0:
                    receipt_action_timings[call_id] = float(elapsed)
            elif isinstance(elapsed, (int, float)) and elapsed >= 0:
                orphan_receipt_timings.append(float(elapsed))

    for log_path in run_directory.rglob("events.jsonl"):
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            call_id = event.get("call_id")
            if event.get("event") == "tool_result" and event.get("tool") == "play":
                elapsed = event.get("elapsed_seconds")
                if isinstance(elapsed, (int, float)) and elapsed >= 0:
                    if isinstance(call_id, str):
                        action_call_ids.add(call_id)
                        event_action_timings[call_id] = float(elapsed)
                    else:
                        fallback_event_play_timings.append(float(elapsed))

    # A single environment action can appear in transport, reliability and
    # replay logs. Join by tool-call ID and choose one timing source per action.
    action_ids = action_call_ids.union(call_action_timings)
    action_timings = [
        call_action_timings.get(
            call_id,
            receipt_action_timings.get(call_id, event_action_timings.get(call_id)),
        )
        for call_id in action_ids
        if call_id in call_action_timings
        or call_id in receipt_action_timings
        or call_id in event_action_timings
    ]
    if not action_timings:
        action_timings.extend(
            fallback_event_play_timings or fallback_call_play_timings
        )
    # Older reliability logs predate call-id correlation. Use their receipts
    # only when no correlated transport/event timing already represents them.
    if not action_timings and orphan_receipt_timings:
        action_timings.extend(orphan_receipt_timings)

    model_timings = [
        float(event["elapsed_seconds"])
        for event in model_results
        if isinstance(event.get("elapsed_seconds"), (int, float))
        and event["elapsed_seconds"] >= 0
    ]
    usage = [event.get("usage") for event in model_results]
    usage_complete = bool(model_results) and all(
        isinstance(report, dict) for report in usage
    )

    def sum_usage(key: str) -> int | None:
        if not usage_complete:
            return None
        values = [report.get(key) for report in usage]
        if any(not isinstance(value, (int, float)) for value in values):
            return None
        return sum(values)

    def sum_nested_usage(parent: str, key: str) -> int | None:
        if not usage_complete:
            return None
        values = [
            (report.get(parent) or {}).get(key)
            if isinstance(report.get(parent) or {}, dict)
            else None
            for report in usage
        ]
        if any(not isinstance(value, (int, float)) for value in values):
            return None
        return sum(values)

    reasoning_tokens = sum_nested_usage(
        "completion_tokens_details", "reasoning_tokens"
    )
    cached_tokens = sum_usage("prompt_cache_hit_tokens")
    return {
        "model_calls": len(model_results),
        "model_seconds": round(sum(model_timings), 3),
        "model_latency_p50": percentile(model_timings, 0.5),
        "model_latency_p95": percentile(model_timings, 0.95),
        "action_calls": len(action_timings),
        "action_seconds": round(sum(action_timings), 3),
        "action_latency_p50": percentile(action_timings, 0.5),
        "action_latency_p95": percentile(action_timings, 0.95),
        "model_retries": model_retries,
        "model_transport_errors": transport_errors,
        "prompt_tokens": sum_usage("prompt_tokens"),
        "completion_tokens": sum_usage("completion_tokens"),
        "reasoning_tokens": reasoning_tokens,
        "cached_prompt_tokens": cached_tokens,
        "response_models": sorted(
            {
                event.get("returned_model")
                for event in model_results
                if event.get("returned_model")
            }
        ),
    }
