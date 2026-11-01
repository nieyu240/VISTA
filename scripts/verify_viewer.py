"""Check the actual local viewer, replay navigation and game assets."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
(root / "reports/deployment").mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://localhost:8765")
base_url = parser.parse_args().base_url.rstrip("/")
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(base_url, wait_until="networkidle")
    page.wait_for_selector("tbody tr")
    assert page.locator("h1").inner_text() == "本地游戏 Benchmark"
    assert "协议" in page.locator("thead").inner_text()
    assert "执行与成本" in page.locator("thead").inner_text()
    api_runs = page.request.get(base_url + "/api/runs").json()
    if api_runs:
        assert {
            "suite",
            "seed",
            "protocol_version",
            "task_identity",
            "clock_mode",
            "model_latency_p95",
            "action_latency_p95",
            "action_calls",
        }.issubset(api_runs[0])
    overview_response = page.request.get(base_url + "/api/overview")
    assert overview_response.status == 200
    overview = overview_response.json()
    if overview.get("matrices"):
        assert "矩阵与配对对比" in page.locator("#overview").inner_text()
    if overview.get("audits") and overview["audits"][0].get("controls"):
        assert {"name", "scored", "progress"}.issubset(
            overview["audits"][0]["controls"][0]
        )
    if overview.get("building_demo_fixture"):
        assert overview["building_demo_fixture"]["score"]["completion"] == 1.0
    page.screenshot(path=str(root / "reports/deployment/dashboard.png"), full_page=True)
    game_response = page.request.get(
        base_url + "/games/18_minecraft-clone-glm/"
    )
    if game_response.status == 200:
        assert "game_api.js" in game_response.text()
    replay_run = next((run for run in api_runs if run.get("replay")), None)
    if replay_run:
        replay_url = base_url + replay_run["replay"]
        page.goto(replay_url, wait_until="load")
        assert page.locator("#frame").count() == 1
        private_url = replay_run["replay"].replace(
            "/replay.html", "/private/api/request_0001.json"
        )
        assert page.request.get(base_url + private_url).status == 404
    browser.close()
print(
    json.dumps(
        {
            "dashboard": True,
            "protocol_columns": True,
            "task_protocol_api": True,
            "matrix_and_audit_overview": True,
            "minecraft_assets": True,
            "replay_navigation": replay_run is not None,
            "private_requests_not_served": replay_run is not None,
        }
    )
)
