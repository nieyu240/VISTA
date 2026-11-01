#!/usr/bin/env python3
"""Audit existing Linux VISTA installations without making model requests.

Run with system Python 3.12 inside WSL. Only the requested output directory is
written. Credentials are inspected for presence; their values are never saved.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import platform
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def redact(text: str) -> str:
    text = re.sub(r"https?://[^/@\s]+@", "https://[REDACTED]@", text)
    text = re.sub(r"(?i)\bsk-[\w-]{8,}", "[REDACTED]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", text)
    return text


def command(args: list[str], cwd: Path | None = None, timeout: int = 30) -> dict:
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return {
            "exit_code": result.returncode,
            "stdout": redact(result.stdout.strip()),
            "stderr": redact(result.stderr.strip()),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit_code": None, "error": type(exc).__name__}


def env_values(root: Path) -> dict[str, str]:
    """Read simple dotenv assignments solely to check presence and probe ARC."""
    path = root / ".env"
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)", line)
        if match:
            values[match[1]] = match[2].strip().strip("\"'")
    return values


def log_categories(run: Path) -> list[str]:
    signatures = {
        "model_not_supported_for_account": "not supported when using Codex",
        "account_usage_limit": "usageLimitExceeded",
        "invalid_api_key": "invalid_api_key",
        "network_timeout": "request timed out",
        "missing_system_bubblewrap": "could not find bubblewrap",
        "missing_auth_file": "Codex auth file not found",
        "missing_pinned_runtime": "Pinned Codex runtime not found",
        "no_arc_environments": "No environments were fetched",
        "invalid_deepseek_dispatch": "has no attribute 'run_task'",
    }
    files = [run / "private/error.txt"]
    files.extend((run / "private/codex_io").glob("*.stderr.txt"))
    categories = set()
    for path in files:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            categories.update(k for k, v in signatures.items() if v in text)
    return sorted(categories)


def history(root: Path) -> list[dict]:
    rows = []
    fields = (
        "game_id", "runtime", "codex_model", "codex_reasoning_effort",
        "created_at", "finished_at", "steps_attempted", "action_attempts",
        "terminal", "termination_reason", "error", "scorecard_closed",
    )
    for run in sorted((root / "runs").glob("run_*")):
        if not run.is_dir():
            continue
        row = {"repo": str(root), "run": run.name}
        path = run / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            row.update(status="incomplete_or_missing_manifest", error_categories=[])
        else:
            row.update({key: manifest.get(key) for key in fields})
            summary = manifest.get("scorecard_summary") or {}
            row.update(
                score=summary.get("score"),
                levels_completed=summary.get("total_levels_completed"),
                environments_completed=summary.get("total_environments_completed"),
                error_categories=log_categories(run),
            )
            if manifest.get("error"):
                row["status"] = "execution_error"
            elif not manifest.get("finished_at"):
                row["status"] = "incomplete_or_missing_manifest"
            elif manifest.get("terminal"):
                row["status"] = "terminal_check_official_score"
            else:
                row["status"] = "stopped_before_terminal"
        row["visible_frames"] = len(list((run / "codex_visible").rglob("*.png")))
        rows.append(row)
    return rows


VENV_PROBE = r'''
import importlib.metadata as md
import importlib.util
import json
import sys
import vista
result = {"python": sys.version.split()[0], "vista_source": vista.__file__}
result["packages"] = {}
for name in ["vista-arc3", "arc-agi", "arcengine", "numpy", "pillow",
             "playwright", "PyYAML", "Jinja2", "regex", "openai", "litellm"]:
    try: result["packages"][name] = md.version(name)
    except md.PackageNotFoundError: result["packages"][name] = None
try:
    from vista.backends.preflight import check_transport
    binary, provenance = check_transport("codex", None)
    result["codex_transport"] = {"ok": True, "binary": str(binary), **provenance}
except Exception as exc:
    result["codex_transport"] = {"ok": False, "error_type": type(exc).__name__}
print(json.dumps(result))
'''


def installation(root: Path) -> dict:
    result = {"path": str(root), "exists": root.is_dir()}
    if not root.is_dir():
        return result
    result["git"] = {
        "head": command(["git", "rev-parse", "HEAD"], root),
        "status": command(["git", "status", "--short", "--branch"], root),
        "remotes": command(["git", "remote", "-v"], root),
        "diff_stat": command(["git", "diff", "--stat"], root),
    }
    values = env_values(root)
    env_file = root / ".env"
    result["dotenv"] = {
        "present": env_file.is_file(),
        "mode": oct(env_file.stat().st_mode & 0o777) if env_file.exists() else None,
        "variables_set": {key: bool(value) for key, value in values.items()},
    }
    py = root / ".venv/bin/python"
    result["venv_exists"] = py.exists()
    if py.exists():
        result["pip_check"] = command([str(py), "-m", "pip", "check"], root)
        probe = command([str(py), "-c", VENV_PROBE], root)
        if probe.get("exit_code") == 0:
            try:
                result["python_environment"] = json.loads(probe["stdout"])
            except ValueError:
                result["python_environment"] = {"error": "invalid_probe_output"}
        else:
            result["python_environment"] = probe
        result["cli_help"] = {}
        for profile in ["arc3", "gameworld", "aigamestore", "babyvision"]:
            probe = command([
                str(root / ".venv/bin/vista"), "--profile", profile,
                "--backend", "codex", "--help",
            ], root)
            result["cli_help"][profile] = {
                "exit_code": probe.get("exit_code"), "error": probe.get("error")
            }
    result["default_data_paths"] = {
        name: (root / "local" / name).exists()
        for name in ["GameWorld", "GameWorld-Games", "aigamestore", "BabyVision"]
    }
    result["external_data_env_set"] = {
        name: bool(values.get(name) or os.getenv(name))
        for name in ["GAMEWORLD_ROOT", "GAMES_ROOT", "AIGAMESTORE_GAMES_ROOT",
                     "BABYVISION_ROOT", "ENVIRONMENTS_DIR"]
    }
    deepseek = root / "src/vista/backends/deepseek/runner.py"
    if deepseek.exists():
        source = deepseek.read_text(encoding="utf-8")
        result["deepseek_integrity_flags"] = {
            "fixed_frame": "standard_arc_vision_frame" in source,
            "keyword_based_success": '"submit" in reply_clean.lower()' in source,
            "dispatcher_called": bool(re.search(r"\bdummy_dispatch\s*\(", source)),
            "warning": "Static indicators require source review; not a valid official score.",
        }
    result["deepseek_batch_log_count"] = len(list((root / "runs").glob("deepseek_*.log")))
    result["runs"] = history(root)
    return result


def request_probe(url: str, proxy: str | None = None, key: str | None = None) -> dict:
    result = {"url": url, "route": "explicit_proxy" if proxy else "direct"}
    handlers = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    opener = urllib.request.build_opener(handlers)
    headers = {"Accept": "application/json", "User-Agent": "VISTA-local-environment-audit"}
    if key:
        headers["X-API-Key"] = key
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=15) as resp:
            result["http_status"] = resp.status
            if url.endswith("/api/games") and key:
                payload = json.loads(resp.read(2_000_000))
                if isinstance(payload, list):
                    result["game_count"] = len(payload)
                    result["sample_game_ids"] = [
                        item.get("game_id", item.get("id"))
                        for item in payload[:5] if isinstance(item, dict)
                    ]
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        # Do not save server responses, request headers, or proxy credentials.
    return result


def container_network_probe(proxy: str | None) -> dict:
    """Check the player's network namespace without mounting auth or source."""
    code = '''
import concurrent.futures,json,urllib.request,urllib.error
proxy=PROXY_VALUE
url="https://chatgpt.com/backend-api/codex/models"
def probe(route):
    handlers=urllib.request.ProxyHandler({"http":proxy,"https":proxy} if route=="explicit_proxy" else {})
    opener=urllib.request.build_opener(handlers)
    result={"route":route,"url":url}
    try:
        with opener.open(url,timeout=15) as response: result["http_status"]=response.status
    except urllib.error.HTTPError as exc: result["http_status"]=exc.code
    except Exception as exc: result["error_type"]=type(exc).__name__
    return result
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    print(json.dumps(list(pool.map(probe,["direct","explicit_proxy"] if proxy else ["direct"]))))
'''.replace("PROXY_VALUE", repr(proxy))
    result = command([
        "docker", "run", "--rm", "--pull", "never", "--network", "bridge",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "arc3-codex-player:0.1", "python3", "-c", code,
    ], timeout=25)
    if result.get("exit_code") == 0:
        try:
            return {"probes": json.loads(result["stdout"])}
        except ValueError:
            return {"error": "invalid_container_probe_output"}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--network", action="store_true", help="GET-only network probes")
    parser.add_argument("--proxy", help="Optional explicit HTTP proxy for network probes")
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "system": {"platform": platform.platform(), "architecture": platform.machine()},
        "tools": {},
        "installations": [],
        "model_requests_made": False,
    }
    for name, argv in {
        "docker": ["docker", "version", "--format", "{{json .}}"],
        "tmux": ["tmux", "-V"], "node": ["node", "--version"],
        "npm": ["npm", "--version"], "git": ["git", "--version"],
    }.items():
        report["tools"][name] = command(argv)
    report["system"]["bwrap_installed"] = shutil.which("bwrap") is not None
    report["system"]["playwright_cache_entries"] = [
        p.name for p in (Path.home() / ".cache/ms-playwright").glob("*")
    ]
    report["system"]["proxy_env_present"] = {
        key: bool(os.getenv(key))
        for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]
    }
    codex = Path.home() / ".local/share/arc3-codex/0.145.0/node_modules/.bin/codex"
    report["codex_version"] = command([str(codex), "--version"])
    report["codex_local_login_status"] = command([str(codex), "login", "status"])
    report["docker_python_smoke"] = command([
        "docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "arc3-codex-player:0.1", "python3", "-c",
        "import platform; print(platform.python_version())",
    ])
    for root in args.repo:
        report["installations"].append(installation(root.resolve()))
    if args.network:
        urls = [
            "https://github.com/joshhhhhan/VISTA",
            "https://raw.githubusercontent.com/joshhhhhan/VISTA/main/README.md",
            "https://chatgpt.com/backend-api/codex/models",
        ]
        jobs = [(url, None, None) for url in urls]
        if args.proxy:
            jobs.extend((url, args.proxy, None) for url in urls)
        # Credentials only go to the known ARC Prize origin, never a custom .env URL.
        arc_key = next((env_values(root).get("ARC_API_KEY") for root in args.repo
                        if env_values(root).get("ARC_API_KEY")), None)
        jobs.append(("https://three.arcprize.org/api/games", None, arc_key))
        if args.proxy:
            jobs.append(("https://three.arcprize.org/api/games", args.proxy, arc_key))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            report["network"] = list(pool.map(lambda job: request_probe(*job), jobs))
        report["container_network"] = container_network_probe(args.proxy)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "environment.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = [run for repo in report["installations"] for run in repo.get("runs", [])]
    columns = ["repo", "run", "status", "game_id", "codex_model", "steps_attempted",
               "action_attempts", "score", "levels_completed", "error", "error_categories"]
    with (args.output / "runs.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "error_categories": ";".join(row["error_categories"])})
    print(json.dumps({"report": str(target), "installations": len(report["installations"]),
                      "historical_runs": len(rows), "network": report.get("network", [])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
