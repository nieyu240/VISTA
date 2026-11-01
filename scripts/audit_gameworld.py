#!/usr/bin/env python3
"""Digest the pinned GameWorld catalog and declare per-adapter control plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path


def sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=root / "reports/deployment/gameworld-catalog/catalog.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "reports/deployment/audits/gameworld-shortcuts.json",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    args.catalog = args.catalog.resolve()
    args.output = args.output.resolve()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    games = catalog.get("games", [])
    rows = []
    rng = random.Random(args.seed)
    for game in games:
        tasks = game.get("tasks", [])
        rows.append(
            {
                "game": game.get("game"),
                "task_count": len(tasks),
                "task_ids": tasks,
                "controls": {
                    "blind_legal_action": "not_run_without environment adapter; no score inferred",
                    "repeat_same_action": "not_run without model; requires fixed task action schema",
                    "text_only_submission": "must remain evaluator-false unless environment ends",
                },
                "task_id_digest": sha(tasks),
                "sampled_task": rng.choice(tasks) if tasks else None,
            }
        )
    payload = {
        "protocol": "benchmark-audit-v1",
        "audit_type": "catalog-and-control-plan",
        "model_calls": 0,
        "score_claims": 0,
        "catalog_digest": sha(catalog),
        "rows": rows,
        "limitations": [
            "This audit does not execute browser actions; it produces a no-model audit manifest.",
            "Action-level blind/repeat controls must be run per adapter because action schemas differ.",
            "A shortcut is not declared merely because a control plan exists.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output), "games": len(rows), "model_calls": 0},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
