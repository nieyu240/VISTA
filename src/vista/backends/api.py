"""OpenAI-compatible vision/tool transport using the real VISTA dispatcher.

This transport runs on the host, exposes only registered public tools, and never
derives a score from an assistant message. The environment remains the evaluator.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from vista.backends.session import BackendResult
from vista.core.contracts import Effect
from vista.reliability import EvidenceHarness


@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    key: str
    model: str
    timeout: float = 90
    max_tokens: int = 2048
    max_calls: int = 100
    task_timeout: float = 1200
    temperature: float = 0
    image_history: int = 8
    strategy: str = "vista"
    seed: int = 42
    thinking: str = "disabled"
    reliability: str = "reliable"
    memory_mode: str = "visual"
    inspection: str = "agent"
    inspection_budget: int = 8
    inspection_view_budget: int = 24
    stall_threshold: int = 3
    recovery_limit: int = 6
    summary_chars: int = 6000
    # Publicly recorded evaluation clock. The current local adapters run with
    # inference paused; wall/token clocks require a runtime that advances while
    # the model is thinking.
    clock_mode: str = "paused"

    @property
    def state_ledger(self) -> bool:
        from vista.reliability import PRESETS

        return PRESETS[self.reliability][0]

    @property
    def verifier(self) -> bool:
        from vista.reliability import PRESETS

        return PRESETS[self.reliability][1]

    @property
    def recovery(self) -> bool:
        from vista.reliability import PRESETS

        return PRESETS[self.reliability][2]

    @classmethod
    def from_env(cls, **kwargs):
        key = (
            os.getenv("VISTA_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        base = (
            os.getenv("VISTA_API_BASE")
            or os.getenv("DEEPSEEK_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.deepseek.com/v1"
        )
        if not key:
            raise ValueError("No API key configured; use --env-file or VISTA_API_KEY")
        parts = urlsplit(base)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
        ):
            raise ValueError("API base must be an HTTP(S) origin without credentials")
        return cls(base.rstrip("/"), key, **kwargs)

    def public(self):
        return {k: v for k, v in vars(self).items() if k != "key"}


class ProviderError(RuntimeError):
    pass


class RunBudget(RuntimeError):
    pass


def append_event(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
    path.chmod(0o600)


def image_blocks(images):
    blocks = []
    for item in images:
        if item.get("steer_text"):
            blocks.append({"type": "text", "text": item["steer_text"]})
        blocks.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{item['mime_type']};base64,{item['data']}",
                    "detail": "high",
                },
            }
        )
    return blocks


def user_content(text, images=()):
    return [{"type": "text", "text": text}, *image_blocks(images)]


def native_tools(definitions):
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": d.get("inputSchema", d.get("input_schema", {})),
            },
        }
        for d in definitions
    ]


class ChatClient:
    def __init__(self, settings: ApiSettings, directory: Path):
        self.settings, self.directory = settings, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.started = time.monotonic()
        self.usage_reports = []
        self.last_elapsed = None
        # API calls are the only retried operation; game actions are never retried.
        self.http = requests.Session()

    def complete(self, messages, tools):
        s = self.settings
        if (
            self.calls >= s.max_calls
            or time.monotonic() - self.started >= s.task_timeout
        ):
            raise RunBudget("model_call_or_wall_clock_budget_exhausted")
        self.calls += 1
        payload = {
            "model": s.model,
            "messages": messages,
            "temperature": s.temperature,
            "max_tokens": s.max_tokens,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
        # Official DeepSeek exposes this setting. Other gateways need not accept it.
        if urlsplit(s.base_url).hostname == "api.deepseek.com":
            payload["thinking"] = {"type": s.thinking}
        request_path = self.directory / f"request_{self.calls:04d}.json"
        request_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        request_path.chmod(0o600)
        started = time.monotonic()
        for attempt in range(3):
            remaining = s.task_timeout - (time.monotonic() - self.started)
            if remaining <= 0:
                raise RunBudget("wall_clock_budget_exhausted")
            try:
                response = self.http.post(
                    s.base_url + "/chat/completions",
                    json=payload,
                    headers={"Authorization": "Bearer " + s.key},
                    timeout=(min(15, remaining), min(s.timeout, remaining)),
                )
            except requests.RequestException:
                # A timed-out model request may have been billed; preserve this fact.
                append_event(
                    self.directory / "calls.jsonl",
                    {
                        "event": "model_transport_error",
                        "call": self.calls,
                        "attempt": attempt + 1,
                        "elapsed_seconds": time.monotonic() - started,
                    },
                )
                if attempt == 2:
                    raise ProviderError("model_network_error") from None
                time.sleep(min(2**attempt, max(0, remaining)))
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                append_event(
                    self.directory / "calls.jsonl",
                    {
                        "event": "model_retry",
                        "call": self.calls,
                        "attempt": attempt + 1,
                        "http_status": response.status_code,
                    },
                )
                time.sleep(min(2**attempt, max(0, remaining)))
                continue
            if not response.ok:
                # Do not persist provider error bodies, which may echo credentials.
                code = {
                    401: "invalid_api_key",
                    402: "insufficient_balance",
                    403: "model_permission_denied",
                    429: "provider_rate_limit",
                }.get(response.status_code, "model_request_rejected")
                raise ProviderError(f"{code}: HTTP {response.status_code}")
            try:
                body = response.json()
                message = body["choices"][0]["message"]
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    raise ValueError()
            except (ValueError, KeyError, IndexError, TypeError):
                raise ProviderError("invalid_model_response") from None
            response_path = self.directory / f"response_{self.calls:04d}.json"
            response_path.write_text(
                json.dumps(body, ensure_ascii=False), encoding="utf-8"
            )
            response_path.chmod(0o600)
            usage = body.get("usage")
            self.usage_reports.append(usage)
            self.last_elapsed = time.monotonic() - started
            append_event(
                self.directory / "calls.jsonl",
                {
                    "event": "model_result",
                    "call": self.calls,
                    "elapsed_seconds": self.last_elapsed,
                    "requested_model": s.model,
                    "returned_model": body.get("model"),
                    "usage": usage,
                    "finish_reason": body["choices"][0].get("finish_reason"),
                },
            )
            return message
        raise ProviderError("model_retries_exhausted")


def trim_images(messages, keep):
    """Retain text and tool references; original images remain in saved requests."""
    result = copy.deepcopy(messages)
    count = 0
    for message in reversed(result):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for index in range(len(content) - 1, -1, -1):
            block = content[index]
            if block.get("type") == "image_url":
                count += 1
                if count > keep:
                    digest = hashlib.sha256(
                        block["image_url"]["url"].encode()
                    ).hexdigest()[:12]
                    content[index] = {
                        "type": "text",
                        "text": f"[Earlier image {digest} archived; use inspect/history to revisit.]",
                    }
    return result


VISTA_HINT = (
    "Use brief hypotheses, test one action, compare the resulting view, and revise. "
    "If an action has no effect, inspect and change the hypothesis instead of repeating it. "
    "Keep verified rules in write_guide and current plan in write_working. "
    "Use inspect/read_pixels when small visual details matter. "
    "Do not claim success: the game evaluator determines it."
)


class ApiSession:
    """Duck-typed BackendSession for the upstream GameWorld/GameStore loops."""

    def __init__(self, runtime, directory: Path, settings: ApiSettings):
        self.runtime, self.settings = runtime, settings
        self.client = ChatClient(settings, directory)
        self.reliability = EvidenceHarness(settings, directory / "reliability")
        self.usage_reports = self.client.usage_reports
        self.session_id = "api-" + uuid.uuid4().hex
        system = (
            runtime.session.task.objective
            + "\n\n"
            + runtime.experiment.profile.prompts.system
        )
        if settings.strategy == "vista":
            system += "\n\n" + VISTA_HINT
        reliability_instructions = self.reliability.instructions()
        if reliability_instructions:
            system += (
                "\n\n# Reliability and evidence protocol\n" + reliability_instructions
            )
        self.system_prompt = system
        self.messages = [{"role": "system", "content": system}]
        self.reliability.write_configuration(runtime.tools(), system)

    def run(self, task_input):
        runtime = self.runtime
        self.reliability.begin_decision()
        # The current-observation and bounded-summary conditions deliberately
        # discard old conversational turns. Archived images remain on disk.
        if self.settings.memory_mode in {"current", "summary"}:
            self.messages = [{"role": "system", "content": self.system_prompt}]
        runtime.begin_turn(task_input)
        images = self.reliability.present(
            runtime.context.images(task_input.observation), source="observation"
        )
        public_context = self.reliability.public_context()
        guidance = self.reliability.guidance()
        prefix = "\n\n".join(x for x in (guidance, public_context) if x)
        text = (prefix + "\n\n" if prefix else "") + task_input.text
        self.messages.append({"role": "user", "content": user_content(text, images)})
        tools = native_tools(self.reliability.definitions(runtime.tools()))
        try:
            while not runtime.terminal:
                message = self.client.complete(
                    trim_images(self.messages, self.settings.image_history), tools
                )
                self.reliability.model_response(
                    self.client.calls,
                    elapsed_seconds=self.client.last_elapsed,
                    usage=(self.client.usage_reports[-1]
                           if self.client.usage_reports else None),
                )
                self.messages.append(message)
                calls = message.get("tool_calls") or []
                if not calls:
                    self.reliability.text_only(message.get("content") or "")
                    return BackendResult(
                        0, message.get("content") or "", self.session_id
                    )
                action_executed = False
                result_images = []
                for call in calls:
                    call_id = call.get("id") or uuid.uuid4().hex
                    name = call.get("function", {}).get("name", "")
                    try:
                        arguments = json.loads(call["function"]["arguments"])
                    except (ValueError, KeyError, TypeError):
                        arguments = None
                    self.reliability.bind_tool_call(call_id, name)
                    spec = runtime.spec(name)
                    is_action = bool(spec and spec.effect == Effect.ENVIRONMENT)
                    before_revision = runtime.state_revision
                    started = time.monotonic()
                    if name == "report_state":
                        text, accepted = self.reliability.report(arguments)
                        images = ()
                        execution = None
                    else:
                        guard = self.reliability.tool_guard(name, arguments)
                        if guard:
                            text, accepted, images = guard, False, ()
                            execution = None
                        elif action_executed or runtime.terminal:
                            text, accepted, images = (
                                "Observe the preceding action before another action.",
                                False,
                                (),
                            )
                            execution = None
                        else:
                            execution = runtime.execute(name, arguments, call_id)
                            text, accepted, images = (
                                execution.text,
                                execution.success,
                                execution.images,
                            )
                            action_executed = bool(
                                spec and spec.effect == Effect.ENVIRONMENT
                            )
                            runtime.acknowledge(call_id)
                    elapsed = time.monotonic() - started
                    self.reliability.tool_attempt(
                        name,
                        arguments,
                        call_id=call_id,
                        is_action=is_action,
                        accepted=accepted,
                        executed=is_action
                        and runtime.state_revision > before_revision,
                        environment_step_id=(
                            runtime.state_revision if is_action else None
                        ),
                        reason=None if accepted else text,
                    )
                    if name != "report_state" and execution is not None:
                        if spec and spec.effect == Effect.ENVIRONMENT:
                            self.reliability.action(
                                name,
                                arguments,
                                text,
                                accepted,
                                elapsed,
                                call_id=call_id,
                                environment_step_id=runtime.state_revision,
                            )
                    if images:
                        images = self.reliability.present(images, source="action")
                    self.messages.append(
                        {"role": "tool", "tool_call_id": call_id, "content": text}
                    )
                    result_images.extend(images)
                    append_event(
                        self.client.directory / "calls.jsonl",
                        {
                            "event": "action_result",
                            "call_id": call_id,
                            "tool": name,
                            "arguments": arguments,
                            "elapsed_seconds": time.monotonic() - started,
                            "result": text,
                        },
                    )
                # All tool replies must precede the next user/image message.
                if result_images:
                    self.messages.append(
                        {
                            "role": "user",
                            "content": user_content("Tool result view:", result_images),
                        }
                    )
                if action_executed or runtime.terminal or runtime.boundary:
                    return BackendResult(0, "", self.session_id, runtime.boundary)
        except RunBudget:
            runtime.stop("api_budget_exhausted")
            return BackendResult(0, "", self.session_id, "api_budget_exhausted")
        except ProviderError as exc:
            runtime.stop(str(exc), failed=True)
            return BackendResult(1, "", self.session_id)
        return BackendResult(0, "", self.session_id, "task_complete")
