# API 与评测协议参考

统一入口为 `python -m vista`（等同于 `python -m vista.localbench`），命令有 `doctor`、`list`、`run`、`compare`、`replay`。Windows 的 `scripts/benchmark.ps1` 另外提供 `report`。

## 模型配置

从显式 `--env-file .env` 读取配置；已存在的进程环境变量优先。命令行 `--model` 优先于 `VISTA_MODEL`，缺省模型 ID 为 `deepseek-flash`。

| 环境变量 | 含义 |
| --- | --- |
| `VISTA_API_KEY` | 服务商密钥；兼容 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 回退 |
| `VISTA_API_BASE` | Chat Completions 基础地址；兼容 DeepSeek / OpenAI 地址变量 |
| `VISTA_MODEL` | 服务商提供的真实模型 ID |
| `ARC_API_KEY` | ARC3 官方在线服务密钥，仅 ARC3 使用 |
| `VISTA_BENCH_DATA` | WSL / Linux 游戏数据目录 |

密钥不写入 manifest 或模型请求 JSON；请求日志包含任务输入和服务商响应，默认保留在 Git 忽略的运行目录。API 只向模型开放注册的任务工具。

## 主要参数

| 参数 | 默认值 / 作用 |
| --- | --- |
| `--profile` | `gameworld`；也可选 `aigamestore` / `arc3` |
| `--game` / `--task` | 用 `list` 查询；GameWorld 需要指定对应任务 |
| `--seed` | `42`；控制环境，不能保证服务商推理确定 |
| `--output` | 新运行目录；运行与对比时必填，不覆盖现有数据 |
| `--max-calls` | `150` 次逻辑模型请求；传输重试另外记账 |
| `--max-tokens` | `2048`，单次输出 token 上限 |
| `--timeout` / `--task-timeout` | 单次请求 90 秒 / 会话 1200 秒；启动和清理另有开销 |
| `--max-steps` | `100`；GameWorld 固定为原生 100 次决策，GameStore 最多 120 次决策，ARC3 为动作上限 |
| `--strategy` | `vista` / `baseline`；额外提示词条件，两者都有注册工具 |
| `--thinking` | `disabled` / `enabled`；只向官方 DeepSeek 地址发送对应扩展参数 |
| `--image-history` | `8`；保留在请求中的图像数量，原图仍归档 |
| `--clock-mode` | 当前仅 `paused`；`wall` / `token` 在适配器支持前拒绝运行 |

`doctor` / `list` 不生成模型响应。`doctor --probe-model --env-file .env --output runs/model-check` 会发起一次使用图像与工具的付费模型检查。

## 记忆、核验与恢复

| 参数 | 可选值与意义 |
| --- | --- |
| `--reliability` | `legacy` 关闭扩展；`ledger` 状态卡；`verified` 加完成核验；`reliable` 再加有界恢复 |
| `--memory-mode` | `current` 当前观察；`summary` 有界状态 / 回执文字；`visual` 视觉历史与回看；`legacy` 原对话行为 |
| `--inspection` | `off` 关闭，`agent` 自主检查；`fixed` 为预留配置，目前不自动产生定时检查 |
| `--inspection-budget` | `8` 次检查；`--inspection-view-budget` 默认 24 个视图 |
| `--stall-threshold` / `--recovery-limit` | 默认 3 / 6，控制无进展提示和恢复上限 |

`report_state` 要求主张引用当前 episode 中的观测帧和区域，记录子目标、预测与自我核验。它不能读取私有 evaluator，也不能设置成功。引用可追溯不等于语义证据正确；状态卡不代替环境真值。

## 动作与结果

- 图像来自真实环境。一次模型响应至多执行一个环境动作，多余动作返回未执行回执。
- 非法参数、重复调用 ID 不推动环境；网络重试只重试模型请求，游戏动作不自动重试。
- 模型宣称完成不会加分。GameWorld / GameStore 由原生 evaluator 计分；ARC3 保存官方 scorecard 与通过关卡数。
- 任务失败、预算耗尽、基础设施错误保留记录。`scored=false` 不应作为成功样本，也不应从统计分母中悄悄删除。

`task-protocol-v2` 保存任务、种子、预算、数据版本、评估器和计时模式；源码、工具 schema、提示词与模块设置另存 manifest / `reliability-config.json`。环境动作、观察、状态卡、模型响应与动作回执使用关联 ID 和时间戳。

报告分别展示原生进度 / 成功、合法动作率、文字轮次、证据引用覆盖、检查与恢复次数、调用数、token 和模型 / 动作延迟 p50/p95。没有独立真值的证据语义准确率、状态准确率、恢复成功率和严格成功率为 `null`。

同任务同种子条件才进入配对分析；任务先聚合，再按任务及任务内种子进行两级 bootstrap。少于两个任务时不估计置信区间，不混合不同套件的原生评分尺度。回放查看保存的画面与事件，不重新执行游戏引擎。
