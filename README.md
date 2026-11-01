# VISTA Local Game Benchmark

**Shareable project:** [nieyu240/VISTA, `benchmark/local-evaluation` branch](https://github.com/nieyu240/VISTA/tree/benchmark/local-evaluation)

**中文说明：** [已完成工作、实测结果与剩余事项](docs/项目工作总结.md)

This branch packages a local, reproducible game-agent benchmark built on the
upstream VISTA project. It keeps each environment's native evaluator as the
source of task scores, then adds traceable process diagnostics, repeatable
experiments, a local results dashboard, and a replay viewer.

## What Is Included

- **Environments:** pinned GameWorld tasks (34 games, 170 tasks), AI GameStore
  browser games (10 games), and ARC-AGI-3 through its official online API.
  The Minecraft-like GameWorld environment is a browser clone, not Minecraft.
- **Agent runner:** an OpenAI-compatible vision/tool backend that calls the
  environment's registered tools. It does not expose shell access or private
  evaluator state to the model.
- **Reliability conditions:** `legacy`, `ledger`, `verified`, and `reliable`
  presets; current, summary, and visual-memory conditions; bounded inspection
  and recovery budgets; public state claims linked to observed frame regions.
- **Auditable traces:** observations, model responses, tool calls, action
  receipts, event timestamps, token usage, model/action latency, rejected
  actions, retries, and transport errors are joined by run and call IDs.
- **Task and experiment records:** versioned task identities include the task,
  seed, budgets, dataset revision, evaluator, and clock mode. Matrix runs can
  resume after interruption without replacing partial traces; reports compare
  matched task/seed pairs and preserve failed attempts.
- **Benchmark validity tools:** text-only, repeated-action, and seeded blind
  controls; a host-side building-state scorer; a local dashboard; and a saved
  trace/replay viewer.
- **Research notes:** [benchmark improvement plan](docs/论文启发下的Benchmark完善方案.md),
  [custom game integration plan](docs/自定义游戏benchmark方案.md), and
  [API runner details](docs/local-api.md).

## What Has Been Measured

The current local evidence is diagnostic, not a leaderboard result:

- On GameWorld Minecraft-like task `18_01`, seed 42, one basic-prompt run
  scored 17/20 requested dirt (85%); one enhanced-prompt run scored 20/20
  (100%). One paired seed does not establish a repeatable gain.
- On 2048 task `01_01`, the saved baseline, enhanced, and reliability runs all
  reached the target tile. Their action counts, model calls, and elapsed time
  differ, so these runs are not collapsed into one efficiency claim.
- The ARC-AGI-3 `vc33` thinking run received the official score `0.303819` and
  completed 1 of 7 levels; it did not finish the game.
- Text-only, repeated-action, and seeded-blind controls each scored 0% on the
  single GameWorld `18_01`, seed 42 audit. This does not prove every task is
  resistant to shortcuts.
- An 18-job, two-task/three-seed/three-condition MC matrix is prepared in
  `configs/mc-development-matrix.json`. It remains a plan and has not been run.

Native evaluator scores are never replaced by process metrics. Evidence
semantic accuracy, hidden-state accuracy, and strict success remain missing
until independent labels or task constraints define them. No model experiment
is run by the deployment or report-generation commands.

## Setup

The included deployment path targets Windows with WSL Ubuntu 24.04. From a
PowerShell prompt:

```powershell
git clone --branch benchmark/local-evaluation https://github.com/nieyu240/VISTA.git
cd VISTA
Copy-Item .env.example .env
# Add your own VISTA_API_KEY (or DEEPSEEK_API_KEY) to the ignored .env file.
.\scripts\deploy.ps1
.\scripts\benchmark.ps1 -Command doctor
.\scripts\benchmark.ps1 -Command list -Profile gameworld
.\scripts\benchmark.ps1 -Command run -Profile gameworld `
  -Game 18_minecraft-clone-glm -Task 18_01 -ModelEnvFile .env
.\scripts\open_benchmark.ps1
```

Deployment downloads the pinned benchmark datasets into
`$HOME/.local/share/vista-benchmark` in WSL and installs Python dependencies
in `$HOME/.venvs/vista-benchmark`. Override these locations with
`VISTA_BENCH_DATA` and `VISTA_BENCH_VENV`. Model calls can incur provider
charges. Run output is saved under `runs/` and is ignored by Git because it can
contain prompts, screenshots, provider responses, scorecard identifiers, and
machine-specific data.

The proxy helper preserves existing network settings by default. To use a
Windows proxy from WSL, set `VISTA_AUDIT_PROXY` inside WSL to its reachable URL.

To create an experiment plan without calling a model:

Run this block from WSL at the cloned repository root:

```bash
cd /path/to/VISTA
"$HOME/.venvs/vista-benchmark/bin/python" scripts/run_matrix.py \
  --matrix configs/mc-development-matrix.json \
  --output runs/matrix-mc-development
```

See [API runner and protocol notes](docs/local-api.md) for other profiles,
budgets, evidence fields, and scoring limitations.

Run the offline tests and style check with the installed environment:

```bash
"$HOME/.venvs/vista-benchmark/bin/python" -m unittest discover -s tests -v
"$HOME/.venvs/vista-benchmark/bin/ruff" check src/vista scripts tests
```

## Boundaries

- The browser Minecraft-like game is not a deployment of Minecraft. No
  Minecraft, Don't Starve, or Terraria server/client adapter is included.
- The building scorer is tested with a fixture; a live game-state adapter is
  still needed for Agent construction scores.
- The local browser adapters support paused inference timing. Wall-clock and
  token-clock evaluations are rejected until an environment adapter advances
  during inference.
- Saved replay displays the recorded trace; it does not deterministically
  re-execute the game engine.
- Game assets remain governed by their upstream licenses and `RIGHTS` files.
  Local deployment does not grant redistribution rights.

## Repository Layout

- `src/vista/localbench.py`: local model-backed runner and task manifest.
- `src/vista/reliability.py`: public evidence ledger and diagnostics.
- `src/vista/backends/api.py`: OpenAI-compatible API transport and tool loop.
- `src/vista/benchmarks/gameworld/building.py`: host-side building evaluator.
- `scripts/`: deployment, matrix, audit, report, replay, and local viewer tools.
- `dashboard/`: local browser dashboard.
- `configs/`: matrix and building task definitions.
- `tests/`: offline regression tests.
- `docs/UPSTREAM_VISTA_README.md`: upstream VISTA project README.
