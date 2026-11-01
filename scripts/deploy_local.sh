#!/usr/bin/env bash
# Deploy the fork and its pinned browser games in an isolated WSL environment.
set -euo pipefail
benchmark_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
benchmark_repo="$benchmark_root"
benchmark_venv="${VISTA_BENCH_VENV:-$HOME/.venvs/vista-benchmark}"
benchmark_data="${VISTA_BENCH_DATA:-$HOME/.local/share/vista-benchmark}"
mkdir -p "$benchmark_data" "$benchmark_root/reports/deployment"
[[ -f "$benchmark_repo/pyproject.toml" ]] || {
  echo "Run this script from the checked-out benchmark project." >&2
  exit 2
}
python3 -m venv "$benchmark_venv"
"$benchmark_venv/bin/python" -m pip install -e "$benchmark_repo[gameworld,aigamestore,dev]"
"$benchmark_venv/bin/python" -m playwright install chromium
"$benchmark_venv/bin/python" - "$benchmark_repo" "$benchmark_data" <<'PY'
import concurrent.futures
import re
import subprocess
import sys
from pathlib import Path

repo, data = map(Path, sys.argv[1:])
source = (repo / 'src/vista/benchmarks/gameworld/upstream.py').read_text()
pins = dict(re.findall(r'^(GAMEWORLD_REVISION|GAMES_REVISION) = "([a-f0-9]+)"', source, re.M))
jobs = [
    ('https://github.com/gameworld-project/GameWorld.git', 'GameWorld', pins['GAMEWORLD_REVISION']),
    ('https://github.com/gameworld-project/GameWorld-Games.git', 'GameWorld-Games', pins['GAMES_REVISION']),
    ('https://github.com/lance-ying/aigamestore_harness.git', 'aigamestore', '5a7c7a2c536c3599cd2e5b7b1987a382392ca0f2'),
]

def checkout(job):
    url, name, revision = job
    dest = data / name
    if not dest.exists():
        subprocess.run(['git', 'init', '-q', str(dest)], check=True)
        subprocess.run(['git', '-C', str(dest), 'remote', 'add', 'origin', url], check=True)
    dirty = subprocess.check_output(['git', '-C', str(dest), 'status', '--porcelain'], text=True)
    if dirty.strip():
        raise RuntimeError(f'Refusing to replace local changes in {dest}')
    present = subprocess.run(['git', '-C', str(dest), 'cat-file', '-e', revision], capture_output=True)
    if present.returncode:
        subprocess.run(['git', '-C', str(dest), 'fetch', '--depth', '1', 'origin', revision], check=True)
    subprocess.run(['git', '-C', str(dest), 'checkout', '-q', '--detach', revision], check=True)
    print(f'Deployed {name}: {revision}', flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    list(pool.map(checkout, jobs))
PY
"$benchmark_venv/bin/python" -m pip check
"$benchmark_venv/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    print('Chromium ready:', browser.version)
    browser.close()
PY
echo "Deployment complete: $benchmark_repo"
