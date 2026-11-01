#!/usr/bin/env python3
"""Loopback-only viewer for locally deployed games and saved benchmark results."""

import argparse
import http.server
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from metrics import call_metrics


def summarize(path, root):
    row = json.loads(path.read_text(encoding="utf-8"))
    protocol_path = path.parent / "task_protocol.json"
    task_protocol = (
        json.loads(protocol_path.read_text(encoding="utf-8"))
        if protocol_path.is_file()
        else None
    )
    gameworld = "role_steps" in row
    action_count = 0 if gameworld else None
    text_only_count = 0 if gameworld else None
    if gameworld:
        for log in path.parent.glob("role-*/artifacts/events.jsonl"):
            for line in log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("event") == "final_response":
                    text_only_count += 1
                if (
                    event.get("event") == "evidence_event"
                    and event.get("environment")
                    and event.get("action") is not None
                    and event.get("success") is True
                ):
                    action_count += 1
    return {
        "run": str(path.parent.relative_to(root / "runs")),
        "game": row.get("game_id", row.get("game", row.get("game_number"))),
        "task": row.get("task_id"),
        "seed": row.get("seed", (task_protocol or {}).get("seed")),
        "suite": (task_protocol or {}).get("suite"),
        "protocol_version": (task_protocol or {}).get("protocol"),
        "clock_mode": (task_protocol or {}).get("clock_mode", "legacy"),
        "task_identity": row.get(
            "task_identity", (task_protocol or {}).get("task_identity")
        ),
        "task_protocol": task_protocol,
        "model": row.get("model", row.get("settings", {}).get("model")),
        "scored": row.get("scored"),
        "success": row.get("success"),
        "progress": row.get("progress"),
        "official_score": row.get("official_score"),
        "normalized_score": row.get("normalized_score"),
        "raw_score": row.get("raw_score"),
        "levels_completed": row.get("levels_completed"),
        "total_levels": row.get("total_levels"),
        "steps": row.get("steps", row.get("role_steps", row.get("decisions"))),
        "step_kind": "decision" if gameworld or "decisions" in row else "action",
        "executed_actions": action_count,
        "text_only_turns": text_only_count,
        "elapsed_seconds": row.get("elapsed_seconds"),
        **call_metrics(path.parent),
        "error": row.get("error"),
        "reason": row.get("termination_reason"),
        "reliability": row.get("reliability"),
        "replay": "/runs/"
        + str(path.parent.relative_to(root / "runs")).replace("\\", "/")
        + "/replay.html"
        if (path.parent / "replay.html").is_file()
        else None,
    }


class Viewer(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        route = unquote(urlsplit(self.path).path)
        if route == "/api/runs":
            rows = []
            for path in sorted((self.server.workspace / "runs").rglob("result.json")):
                try:
                    rows.append(summarize(path, self.server.workspace))
                except (ValueError, OSError):
                    continue
            for row in rows:
                row.pop("task_protocol", None)
            return self.send_bytes(
                json.dumps(rows, ensure_ascii=False).encode(), "application/json"
            )
        if route == "/api/overview":
            result_path = self.server.workspace / "reports/deployment/results.json"
            if not result_path.is_file():
                return self.send_bytes(b"{}", "application/json")
            report = json.loads(result_path.read_text(encoding="utf-8"))
            return self.send_bytes(
                json.dumps(
                    {
                        "updated_at": report.get("updated_at"),
                        "matrices": report.get("matrices", []),
                        "audits": report.get("audits", []),
                        "building_demo_fixture": report.get("building_demo_fixture"),
                        "scope": report.get("scope"),
                    },
                    ensure_ascii=False,
                ).encode(),
                "application/json",
            )
        if route == "/":
            path = self.server.workspace / "dashboard/index.html"
        elif route.startswith("/games/"):
            base = (self.server.data / "GameWorld-Games/benchmark").resolve()
            path = (base / route.removeprefix("/games/")).resolve()
            if not path.is_relative_to(base):
                return self.send_error(403)
            if path.is_dir():
                path = path / "index.html"
        elif route.startswith("/runs/") and route.endswith("/replay.html"):
            base = (self.server.workspace / "runs").resolve()
            path = (base / route.removeprefix("/runs/")).resolve()
            if not path.is_relative_to(base):
                return self.send_error(403)
        else:
            return self.send_error(404)
        if not path.is_file():
            return self.send_error(404)
        self.send_bytes(
            path.read_bytes(),
            mimetypes.guess_type(str(path))[0] or "application/octet-stream",
        )

    def send_bytes(self, content, content_type):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            content_type
            + ("; charset=utf-8" if content_type.startswith("text/") else ""),
        )
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
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
    args = parser.parse_args()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Viewer)
    server.workspace = Path(__file__).resolve().parents[1]
    server.data = args.data
    print(f"VISTA benchmark: http://localhost:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
