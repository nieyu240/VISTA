# 团队游戏 Benchmark

给游戏 Agent 提供可复用的评测工具：接入模型、执行真实动作、读取环境原生分数、保存完整轨迹，并比较成功率、动作数、延迟与 token。

## 可以直接用什么

| 功能 | 使用方式 |
| --- | --- |
| 单次评测 / 提示对比 | `scripts/benchmark.ps1` 或 `python -m vista` |
| 批量任务与多种子对比 | `scripts/run_matrix.py`，支持断点续跑 |
| 原生计分与过程诊断 | 自动输出任务进度、成功状态、决策 / 动作、状态卡、核验与恢复记录 |
| 汇总报告与日志回放 | `scripts/report_results.py`、本地仪表盘、每次运行的 `replay.html` |
| 有效性审计 | `scripts/run_controls.py`，运行不调用模型的控制策略 |
| 建筑状态评分 | `scripts/score_building.py`，比较导出的方块状态与目标 |

支持 GameWorld（34 游戏、170 任务）、AI GameStore（10 游戏）和 ARC-AGI-3 官方在线 API。模型服务须支持图像输入、Chat Completions 和原生工具调用。

## 1. 安装

Windows 10/11，需要 Git 和 WSL Ubuntu 24.04。首次使用 WSL 时，在管理员 PowerShell 执行 `wsl --install -d Ubuntu-24.04`，按提示重启并创建 Linux 用户。

在 PowerShell 执行：

```powershell
git clone --branch benchmark/local-evaluation https://github.com/nieyu240/VISTA.git
cd VISTA
.\scripts\deploy.ps1
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填写自己的 `VISTA_API_KEY`、`VISTA_API_BASE` 和 `VISTA_MODEL`。不要提交 `.env`。

部署会安装 Python 3.12 环境、Chromium 和固定版本的游戏数据；首次下载需要联网。Linux Ubuntu 用户见[团队使用手册](docs/团队使用手册.md)。

## 2. 检查与运行

```powershell
# 安装检查、列出任务；这两步不调用模型
.\scripts\benchmark.ps1 -Command doctor
.\scripts\benchmark.ps1 -Command list -Profile gameworld

# 2048 示例；从这里开始会使用模型服务额度
.\scripts\benchmark.ps1 -Command run -Profile gameworld `
  -Game 01_2048 -Task 01_01 -ModelEnvFile .env

# MC 风格采集任务，使用新目录保存结果
.\scripts\benchmark.ps1 -Command run -Profile gameworld `
  -Game 18_minecraft-clone-glm -Task 18_01 -ModelEnvFile .env

# 打开结果和回放页面
.\scripts\open_benchmark.ps1
```

运行默认写入本项目 `runs/`，不会覆盖既有轨迹。部署后也可以双击 `打开Benchmark.cmd` 查看结果。

## 3. 团队复用

- **换模型**：修改 `.env`，或单次传 `-Model 模型名称`。
- **换任务**：从 `list` 中选游戏和任务，再修改 `-Game` / `-Task`。
- **批量实验**：复制 `configs/mc-development-matrix.json`，修改任务、种子、条件和模型；先生成计划，再显式执行。
- **看结果**：原生评分以 `result.json` 为准；`scored=false` 表示没有有效评分。不同套件分别比较。
- **接入新游戏 / 建筑任务**：按[使用手册](docs/团队使用手册.md)对接观察、动作、状态和检查器。

详细命令：[团队使用手册](docs/团队使用手册.md) · 参数与计分协议：[API 参考](docs/local-api.md)

## 使用边界

- MC 风格环境是浏览器复刻游戏；正式 Minecraft、饥荒、泰拉瑞亚需要另外接入。
- 建筑评分器读取主机导出的状态；示例 JSON 用于校验评分器，不能当作 Agent 建造成绩。
- 当前推理期间暂停环境；轨迹回放用于查看保存的日志和画面。
- 证据语义准确率、隐藏状态准确率和严格成功率需要独立真值，未实现时保留为空。过程诊断不修改原生分数。
- 示例矩阵是实验配置，分数由团队实际执行产生。API 费用按服务商账单核实。

## 开发检查

在已安装环境的 WSL / Linux 项目目录运行：

```bash
PYTHON="$HOME/.venvs/vista-benchmark/bin/python"
"$PYTHON" -m unittest discover -s tests -v
"$PYTHON" -m ruff check src/vista scripts tests
```

本项目基于 [VISTA](https://github.com/joshhhhhan/VISTA) 裁剪与扩展，保留 [MIT 许可证](LICENSE)。第三方游戏素材在部署时从原仓库下载，遵循各自许可证。
