"""Agent-side evidence, bounded memory and recovery. Never reads evaluator state."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

VERSION = "public-evidence-v1"
PRESETS = {
    "legacy": (False, False, False),
    "ledger": (True, False, False),
    "verified": (True, True, False),
    "reliable": (True, True, True),
}
MEMORY_TOOLS = {
    "history",
    "read_guide",
    "write_guide",
    "read_working",
    "write_working",
    "save_compact_checkpoint",
}
VISUAL_TOOLS = {"inspect", "read_pixels"}

REF = {
    "type": "object",
    "additionalProperties": False,
    "required": ["episode_id", "frame_id", "region"],
    "properties": {
        "episode_id": {"type": "string"},
        "frame_id": {"type": "string"},
        "region": {
            "type": "array",
            "items": {"type": "number", "minimum": 0, "maximum": 1},
            "minItems": 4,
            "maxItems": 4,
        },
    },
}
STATE_TOOL = {
    "name": "report_state",
    "description": (
        "Record estimates based ONLY on supplied visuals. Reference the episode/frame IDs "
        "printed with images. region=[x,y,width,height] uses normalized ORIGINAL image "
        "coordinates, origin top-left. This records YOUR claims, NOT evaluator truth. "
        "Report before an action and after an observed change; keep it brief. "
        "Mark uncertain or hidden facts unknown. verification compares the previous "
        "prediction to visible evidence. It cannot finish a game or award points."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "beliefs",
            "subgoal",
            "prediction",
            "verification",
            "progress",
            "waiting",
            "completion_claim",
        ],
        "properties": {
            "beliefs": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["field", "value", "certainty", "evidence"],
                    "properties": {
                        "field": {"type": "string", "minLength": 1, "maxLength": 100},
                        "value": {"type": "string", "maxLength": 160},
                        "certainty": {"enum": ["observed", "inferred", "unknown"]},
                        "evidence": {"type": "array", "maxItems": 4, "items": REF},
                    },
                },
            },
            "subgoal": {"type": "string", "maxLength": 400},
            "prediction": {"type": "string", "maxLength": 400},
            "verification": {
                "type": "object",
                "additionalProperties": False,
                "required": ["verdict", "reason", "evidence"],
                "properties": {
                    "verdict": {"enum": ["supported", "contradicted", "unknown"]},
                    "reason": {"type": "string", "maxLength": 400},
                    "evidence": {"type": "array", "maxItems": 4, "items": REF},
                },
            },
            "progress": {"enum": ["advanced", "unchanged", "unknown"]},
            "waiting": {"type": "boolean"},
            "completion_claim": {"type": "boolean"},
        },
    },
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class EvidenceHarness:
    """Receives only model-visible images, claims and public tool receipts."""

    def __init__(self, settings, directory: Path, episode_id=None):
        self.settings = settings
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.episode = episode_id or "episode-" + uuid.uuid4().hex
        self.decision = 0
        self.sequence = 0
        self.frames = {}
        self.current_frames = set()
        self.current_event = None
        self.ledger = None
        self.public_summary = []
        self.last_action = None
        self.repeat_actions = 0
        self.unchanged = 0
        self.recoveries = 0
        self.text_streak = 0
        self.inspection_calls = 0
        self.inspection_views = 0
        self.agent_tool_calls = 0
        self.proposed_action_calls = 0
        self.valid_action_calls = 0
        self.rejected_action_calls = 0
        self.tool_attempts = 0
        self.rejected_tool_calls = 0
        self.ledger_decision = None
        self.last_call = None
        self.active_model_call_id = None
        self.active_tool_call_id = None
        self.environment_step_id = None
        self.started = time.monotonic()
        self.validator = Draft202012Validator(STATE_TOOL["inputSchema"])

    def event(self, kind, **payload):
        self.sequence += 1
        row = {
            "protocol": VERSION,
            "episode_id": self.episode,
            "event_id": f"{self.episode}:{self.sequence}",
            "parent_id": self.last_call,
            "decision_id": self.decision,
            "model_call_id": self.active_model_call_id,
            "tool_call_id": self.active_tool_call_id,
            "environment_step_id": self.environment_step_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "monotonic_seconds": time.monotonic() - self.started,
            "visibility": "agent-public",
            "event": kind,
            "event_type": kind,
            **payload,
        }
        with (self.directory / "reliability.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row["event_id"]

    @property
    def enabled(self):
        return (
            self.settings.state_ledger
            or self.settings.verifier
            or self.settings.recovery
        )

    def instructions(self):
        parts = []
        if self.settings.state_ledger:
            parts.append(
                "Use report_state to record concise visual beliefs, evidence, a subgoal and the next expected change. Never confuse initial inventory, current inventory, items gained and items consumed/placed. Mark uncertain counts unknown and inspect their visible digits."
            )
        if self.settings.verifier:
            parts.append(
                "Compare the last prediction with current visual evidence. supported/contradicted are your assessments, not certified truth. Before a completion claim, reconcile the initial and current observations and consumption. Only the environment's public ending signal terminates the task; a claim cannot award success."
            )
        if self.settings.recovery:
            parts.append(
                "When a public recovery notice appears, inspect a relevant frame, change viewpoint/position or revise the subgoal. Repeated wait actions may be intentional: mark waiting=true when justified. Recovery uses the SAME action/call budgets; no automatic game retry or reset."
            )
        if self.settings.memory_mode in {"summary", "visual"}:
            parts.append(
                "Conversation text is bounded at decision boundaries. Only the saved state card and brief public action receipts persist. Use report_state to preserve what matters; do not rely on old free-form reasoning remaining in context."
            )
        return "\n".join(parts)

    def begin_decision(self):
        self.decision += 1
        self.current_frames = set()
        self.last_call = None
        self.active_model_call_id = None
        self.active_tool_call_id = None
        self.environment_step_id = None
        if self.settings.memory_mode == "current":
            self.ledger = None
            self.public_summary = []

    def model_response(self, model_call_id, *, elapsed_seconds=None, usage=None):
        """Attach a model response to the following tool/action trace."""
        identifier = str(model_call_id)
        self.active_model_call_id = identifier
        self.active_tool_call_id = None
        self.event(
            "model_response",
            model_call_id=identifier,
            elapsed_seconds=elapsed_seconds,
            usage=usage,
        )
        self.last_call = identifier

    def bind_tool_call(self, call_id, name):
        """Create an explicit model-response → tool-call edge in the trace."""
        identifier = f"tool:{call_id}"
        self.active_tool_call_id = str(call_id)
        self.event(
            "tool_call",
            tool_call_id=str(call_id),
            tool=name,
            model_call_id=self.last_call,
        )
        self.last_call = identifier

    def present(self, images, *, source="observation"):
        result = []
        for image in images:
            item = copy.deepcopy(image)
            raw = base64.b64decode(item["data"])
            frame_id = "frame-" + hashlib.sha256(raw).hexdigest()[:24]
            label = item.get("steer_text", "")
            # A label is public metadata from the adapter, never a private state read.
            import re

            match = re.search(r'(?:event|turn)="(\d+)"', label)
            if source == "observation" and match:
                self.current_event = int(match.group(1))
            self.frames.setdefault(
                frame_id, {"first_decision": self.decision, "source": source}
            )
            self.current_frames.add(frame_id)
            if self.enabled or self.settings.memory_mode != "legacy":
                item["steer_text"] = (
                    label
                    + f"\nEvidence episode_id={self.episode} frame_id={frame_id}; normalized region [x,y,width,height]."
                )
            self.event(
                "observe",
                frame_id=frame_id,
                image_sha256=hashlib.sha256(raw).hexdigest(),
                source=source,
            )
            result.append(item)
        return result

    def definitions(self, definitions):
        selected = []
        for definition in definitions:
            name = definition["name"]
            if self.settings.memory_mode == "current" and name in MEMORY_TOOLS:
                continue
            if name in VISUAL_TOOLS and (
                self.settings.inspection == "off"
                or self.settings.memory_mode == "summary"
            ):
                continue
            selected.append(definition)
        if self.settings.state_ledger:
            selected.append(copy.deepcopy(STATE_TOOL))
        return selected

    def tool_guard(self, name, arguments):
        if name in MEMORY_TOOLS and self.settings.memory_mode == "current":
            return "History/note tools are disabled by this memory condition."
        if name not in VISUAL_TOOLS:
            return None
        if self.settings.inspection == "off" or self.settings.memory_mode == "summary":
            return "Visual inspection is disabled by this condition."
        if self.settings.inspection == "fixed":
            return "Only scheduled fixed inspections are allowed in this condition."
        views = arguments.get("views", []) if isinstance(arguments, dict) else []
        if self.settings.memory_mode == "current":
            if self.current_event is None or any(
                v.get("event", v.get("turn")) != self.current_event
                or "frame" in v
                or "moment" in v
                for v in views
            ):
                return "Current-observation condition: historical events/moments are unavailable."
        if (
            self.inspection_calls >= self.settings.inspection_budget
            or self.inspection_views + len(views) > self.settings.inspection_view_budget
        ):
            return "The predeclared inspection call/view budget is exhausted."
        self.inspection_calls += 1
        self.inspection_views += len(views)
        self.event("inspection", tool=name, requested_views=len(views))
        return None

    def report(self, arguments):
        self.agent_tool_calls += 1
        if (
            not self.settings.state_ledger
            or self.agent_tool_calls > self.settings.max_calls * 3
        ):
            return "State reporting is disabled or its budget is exhausted.", False
        if not self.validator.is_valid(arguments):
            self.event("belief_rejected", reason="schema")
            return "Invalid report_state schema; no state card was saved.", False
        refs = [r for b in arguments["beliefs"] for r in b["evidence"]] + arguments[
            "verification"
        ]["evidence"]
        for ref in refs:
            x, y, w, h = ref["region"]
            allowed = (
                self.current_frames
                if self.settings.memory_mode == "current"
                else self.frames
            )
            if (
                ref["episode_id"] != self.episode
                or ref["frame_id"] not in allowed
                or w <= 0
                or h <= 0
                or x + w > 1.000001
                or y + h > 1.000001
            ):
                self.event("belief_rejected", reason="unavailable_or_invalid_evidence")
                return (
                    "Evidence must reference a supplied frame in this episode/condition and an in-bounds normalized region.",
                    False,
                )
        previous = self.ledger
        self.ledger = copy.deepcopy(arguments)
        self.ledger_decision = self.decision
        self.event(
            "belief",
            claims=arguments["beliefs"],
            subgoal=arguments["subgoal"],
            completion_claim=arguments["completion_claim"],
        )
        self.event("prediction", text=arguments["prediction"])
        verdict = arguments["verification"]
        if self.settings.verifier:
            self.event(
                "verification",
                assessment=verdict,
                source="agent-self-assessment",
                prior_prediction=previous.get("prediction") if previous else None,
                semantic_certified=False,
            )
        if not arguments["waiting"] and arguments["progress"] == "unchanged":
            self.unchanged += 1
        elif arguments["progress"] == "advanced":
            self.unchanged = 0
        self.event("state_card_saved", state=arguments)
        response = "State card saved. References are structurally valid; semantic truth is NOT certified."
        if self.settings.verifier and (
            verdict["verdict"] == "contradicted" or arguments["completion_claim"]
        ):
            response += " Verify the visual contradiction/count using registered inspection tools; if the public task is still running, continue a legal action rather than repeat a final claim."
        return response, True

    def tool_attempt(
        self,
        name,
        arguments,
        *,
        call_id=None,
        is_action=False,
        accepted=False,
        executed=False,
        environment_step_id=None,
        reason=None,
    ):
        """Record every proposed tool/action, including rejected calls.

        This is deliberately independent of the native evaluator.  It supplies
        the lightweight Invalid Action Rate diagnostic used by recent game
        benchmarks without changing the upstream score or action accounting.
        """
        self.tool_attempts += 1
        if not accepted:
            self.rejected_tool_calls += 1
        if is_action:
            if environment_step_id is not None:
                self.environment_step_id = environment_step_id
            self.proposed_action_calls += 1
            if accepted:
                self.valid_action_calls += 1
            else:
                self.rejected_action_calls += 1
        self.event(
            "tool_attempt",
            tool=name,
            call_id=call_id,
            arguments=arguments,
            is_action=is_action,
            accepted=accepted,
            executed=executed,
            environment_step_id=environment_step_id,
            reason=reason,
        )

    def public_context(self):
        if self.settings.memory_mode in {"legacy", "current"}:
            return ""
        value = {
            "agent_state_estimate": self.ledger,
            "recent_public_receipts": self.public_summary[-4:],
        }
        return (
            "Bounded public memory (agent estimates, not evaluator truth):\n"
            + json.dumps(value, ensure_ascii=False)[: self.settings.summary_chars]
        )

    def guidance(self):
        reasons = []
        if self.settings.verifier and self.text_streak:
            reasons.append(
                "The previous response contained no executable game action. The current task input still exists; check public evidence before claiming completion, then use a registered tool."
            )
        waiting = bool(self.ledger and self.ledger.get("waiting"))
        stalled = self.unchanged >= self.settings.stall_threshold or (
            self.repeat_actions >= self.settings.stall_threshold and not waiting
        )
        if (
            self.settings.recovery
            and stalled
            and self.recoveries < self.settings.recovery_limit
        ):
            self.recoveries += 1
            stage = (
                "inspect relevant current/previous evidence",
                "change viewpoint or position if appropriate",
                "revise the subgoal or hypothesis",
            )[(self.recoveries - 1) % 3]
            reasons.append(
                "Public-pattern recovery notice: repeated action or self-reported unchanged state. This is NOT hidden-progress feedback. Next: "
                + stage
                + ". Intentional waiting can be appropriate; check before changing strategy."
            )
            self.event(
                "recovery",
                trigger="public_action_pattern_or_agent_report",
                stage=stage,
                intervention=self.recoveries,
            )
            self.unchanged = self.repeat_actions = 0
        return "\n".join(reasons)

    def text_only(self, text):
        self.text_streak += 1
        self.event(
            "text_only", text=text[:1200], native_decision_accounting="unchanged"
        )

    def action(
        self,
        name,
        arguments,
        text,
        success,
        elapsed,
        call_id=None,
        environment_step_id=None,
    ):
        signature = digest({"tool": name, "arguments": arguments})
        self.repeat_actions = (
            self.repeat_actions + 1 if signature == self.last_action else 1
        )
        self.last_action = signature
        self.text_streak = 0
        receipt = {
            "tool": name,
            "tool_call_id": call_id,
            "environment_step_id": environment_step_id,
            "arguments": arguments,
            "receipt": text[:300],
            "accepted": success,
        }
        self.public_summary.append(receipt)
        self.public_summary = self.public_summary[-4:]
        self.event(
            "action_receipt",
            **receipt,
            elapsed_seconds=elapsed,
        )

    def write_configuration(self, definitions, system):
        value = {
            "protocol": VERSION,
            "episode_id": self.episode,
            "settings": self.settings.public(),
            "tool_schema_sha256": digest(definitions),
            "system_prompt_sha256": digest(system),
            "state_schema_sha256": digest(STATE_TOOL),
            "private_state_access": False,
            "semantic_evidence_judge": "not_implemented",
        }
        (self.directory / "reliability-config.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def diagnostic_summary(directory: Path):
    rows = []
    for path in directory.rglob("reliability.jsonl"):
        rows.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if not rows:
        return None
    beliefs = [b for r in rows if r["event"] == "belief" for b in r["claims"]]
    actions = [r for r in rows if r["event"] == "action_receipt"]
    observations = [r for r in rows if r["event"] == "observe"]
    return {
        "protocol": VERSION,
        "state_cards": sum(r["event"] == "state_card_saved" for r in rows),
        "claims": len(beliefs),
        "claims_with_references": sum(bool(b["evidence"]) for b in beliefs),
        "evidence_reference_coverage": sum(bool(b["evidence"]) for b in beliefs)
        / len(beliefs)
        if beliefs
        else None,
        "evidence_semantic_accuracy": None,
        "state_accuracy": None,
        "verification_assessments": sum(r["event"] == "verification" for r in rows),
        "semantic_verifier": "agent self-assessment; not independent certification",
        "recovery_interventions": sum(r["event"] == "recovery" for r in rows),
        "recovery_success_rate": None,
        "text_only_turns": sum(r["event"] == "text_only" for r in rows),
        "environment_action_attempts": len(actions),
        "accepted_actions": sum(r["accepted"] is True for r in actions),
        "action_validity": sum(r["accepted"] is True for r in actions) / len(actions)
        if actions
        else None,
        "tool_attempts": sum(r["event"] == "tool_attempt" for r in rows),
        "rejected_tool_calls": sum(
            r["event"] == "tool_attempt" and r.get("accepted") is not True
            for r in rows
        ),
        "proposed_action_calls": sum(
            r["event"] == "tool_attempt" and r.get("is_action") is True
            for r in rows
        ),
        "valid_action_calls": sum(
            r["event"] == "tool_attempt"
            and r.get("is_action") is True
            and r.get("accepted") is True
            and r.get("executed") is True
            for r in rows
        ),
        "invalid_action_rate": (
            sum(
                r["event"] == "tool_attempt"
                and r.get("is_action") is True
                and r.get("accepted") is not True
                for r in rows
            )
            / sum(
                r["event"] == "tool_attempt" and r.get("is_action") is True
                for r in rows
            )
            if any(
                r["event"] == "tool_attempt" and r.get("is_action") is True
                for r in rows
            )
            else None
        ),
        "completion_claims": sum(
            r["event"] == "belief" and r.get("completion_claim") is True
            for r in rows
        ),
        "contradicted_verifications": sum(
            r["event"] == "verification"
            and (r.get("assessment") or {}).get("verdict") == "contradicted"
            for r in rows
        ),
        "delivered_views": len(observations),
        "distinct_delivered_frames": len({r["frame_id"] for r in observations}),
        "inspection_calls": sum(r["event"] == "inspection" for r in rows),
        "inspection_views": sum(
            r.get("requested_views", 0) for r in rows if r["event"] == "inspection"
        ),
        "rejected_state_cards": sum(r["event"] == "belief_rejected" for r in rows),
        "cost_currency": None,
        "strict_success": None,
    }
