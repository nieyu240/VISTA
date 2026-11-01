#!/usr/bin/env python3
"""Summarize every saved real run without combining incompatible score scales."""

import json
import random
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from metrics import percentile
from serve_benchmark import summarize

root = Path(__file__).resolve().parents[1]
rows = []
for path in sorted((root / "runs").rglob("result.json")):
    row = summarize(path, root)
    result = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = path.parent / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    settings = result.get("settings") or manifest.get("transport") or {}
    row["configuration"] = {
        key: settings.get(key)
        for key in (
            "model",
            "strategy",
            "seed",
            "thinking",
            "max_calls",
            "max_tokens",
            "task_timeout",
            "image_history",
            "clock_mode",
        )
    }
    protocol_path = path.parent / "task_protocol.json"
    if protocol_path.is_file():
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        row["task_protocol"] = {
            "suite": protocol.get("suite"),
            "protocol": protocol.get("protocol"),
            "task_identity": protocol.get("task_identity"),
            "initial_observation_hash": protocol.get("initial_observation_hash"),
            "seed": protocol.get("seed"),
            "max_decisions": protocol.get("max_decisions"),
            "max_model_calls": protocol.get("max_model_calls"),
            "max_tool_calls": protocol.get("max_tool_calls"),
            "max_tokens": protocol.get("max_tokens"),
            "request_timeout_seconds": protocol.get("request_timeout_seconds"),
            "task_timeout_seconds": protocol.get("task_timeout_seconds"),
            "clock_mode": protocol.get("clock_mode", "legacy"),
        }
    row["reliability"] = result.get("reliability")
    # Early manifests omitted thinking; use the archived request as evidence.
    if row["configuration"]["thinking"] is None:
        request_path = next(path.parent.rglob("request_0001.json"), None)
        if request_path:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            row["configuration"]["thinking"] = (request.get("thinking") or {}).get(
                "type"
            )
            row["thinking_evidence"] = str(request_path.relative_to(root))
    rows.append(row)


def mean(values):
    return statistics.mean(values) if values else None


def sample_sd(values):
    return statistics.stdev(values) if len(values) > 1 else None


def bootstrap_hierarchical_ci(groups, *, seed):
    """Resample task clusters, then seeds within each task cluster."""
    groups = [list(values) for values in groups if values]
    if len(groups) < 2 or sum(map(len, groups)) < 2:
        return None
    rng = random.Random(seed)
    count = 5000
    samples = [
        statistics.mean(
            statistics.mean(rng.choice(values) for _ in range(len(values)))
            for values in (rng.choice(groups) for _ in range(len(groups)))
        )
        for _ in range(count)
    ]
    return [percentile(samples, 0.025), percentile(samples, 0.975)]


def matrix_analysis(matrix_path):
    """Summarize completed jobs and matched task/seed condition differences."""
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    observations = []
    for job in matrix.get("jobs", []):
        if job.get("status", "pending") == "pending":
            continue
        run_name = job.get("output_run", job.get("run"))
        if not run_name:
            continue
        run_dir = (matrix_path.parent / run_name).resolve()
        if not run_dir.is_relative_to(matrix_path.parent.resolve()):
            continue
        result_path = run_dir / "result.json"
        result = {}
        summary = {}
        try:
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                summary = summarize(result_path, root)
        except (OSError, json.JSONDecodeError, ValueError):
            result = {}
            summary = {}
        condition = job.get("condition") or {}
        task = job.get("task") or {}
        observations.append(
            {
                "condition": condition.get("name", "unnamed"),
                "game": task.get("game", result.get("game_id")),
                "task": task.get("task", result.get("task_id")),
                "seed": job.get("seed", result.get("seed")),
                "scored": result.get("scored") is True,
                "success": result.get("success"),
                "progress": result.get("progress"),
                "decisions": (
                    sum(summary["steps"])
                    if isinstance(summary.get("steps"), list)
                    else summary.get("steps")
                ),
                "actions": summary.get("executed_actions"),
                "elapsed_seconds": summary.get("elapsed_seconds"),
                "model_calls": summary.get("model_calls"),
                "model_latency_p95": summary.get("model_latency_p95"),
                "action_latency_p95": summary.get("action_latency_p95"),
                "tokens": (
                    summary.get("prompt_tokens") + summary.get("completion_tokens")
                    if isinstance(summary.get("prompt_tokens"), (int, float))
                    and isinstance(summary.get("completion_tokens"), (int, float))
                    else None
                ),
            }
        )

    names = sorted({item["condition"] for item in observations})
    condition_summary = []
    by_condition_key = {}
    for name in names:
        attempted = [item for item in observations if item["condition"] == name]
        valid = [item for item in attempted if item["scored"]]
        successes = [
            float(item["success"])
            for item in valid
            if isinstance(item.get("success"), bool)
        ]
        progress = [
            float(item["progress"])
            for item in valid
            if isinstance(item.get("progress"), (int, float))
            and not isinstance(item.get("progress"), bool)
        ]
        decisions = [
            float(item["decisions"])
            for item in valid
            if isinstance(item.get("decisions"), (int, float))
        ]
        actions = [
            float(item["actions"])
            for item in valid
            if isinstance(item.get("actions"), (int, float))
        ]
        elapsed = [
            float(item["elapsed_seconds"])
            for item in valid
            if isinstance(item.get("elapsed_seconds"), (int, float))
        ]
        calls = [
            float(item["model_calls"])
            for item in valid
            if isinstance(item.get("model_calls"), (int, float))
        ]
        p95 = [
            float(item["model_latency_p95"])
            for item in valid
            if isinstance(item.get("model_latency_p95"), (int, float))
        ]
        action_p95 = [
            float(item["action_latency_p95"])
            for item in valid
            if isinstance(item.get("action_latency_p95"), (int, float))
        ]
        tokens = [
            float(item["tokens"])
            for item in valid
            if isinstance(item.get("tokens"), (int, float))
        ]
        condition_summary.append(
            {
                "condition": name,
                "attempted": len(attempted),
                "scored": len(valid),
                "unscored": len(attempted) - len(valid),
                "success_rate": mean(successes),
                "success_sd": sample_sd(successes),
                "progress_mean": mean(progress),
                "progress_sd": sample_sd(progress),
                "decisions_mean": mean(decisions),
                "actions_mean": mean(actions),
                "elapsed_seconds_mean": mean(elapsed),
                "model_calls_mean": mean(calls),
                "model_latency_p95_mean": mean(p95),
                "action_latency_p95_mean": mean(action_p95),
                "tokens_mean": mean(tokens),
            }
        )
        for item in valid:
            key = (item["game"], item["task"], item["seed"])
            by_condition_key.setdefault(name, {})[key] = item

    paired = []
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            left_rows, right_rows = by_condition_key.get(left, {}), by_condition_key.get(
                right, {}
            )
            keys = sorted(set(left_rows) & set(right_rows), key=str)
            progress_by_task = {}
            success_by_task = {}
            for key in keys:
                group = (key[0], key[1])
                left_item, right_item = left_rows[key], right_rows[key]
                if isinstance(left_item.get("progress"), (int, float)) and isinstance(
                    right_item.get("progress"), (int, float)
                ):
                    progress_by_task.setdefault(group, []).append(
                        float(right_item["progress"]) - float(left_item["progress"])
                    )
                if isinstance(left_item.get("success"), bool) and isinstance(
                    right_item.get("success"), bool
                ):
                    success_by_task.setdefault(group, []).append(
                        float(right_item["success"]) - float(left_item["success"])
                    )
            paired.append(
                {
                    "condition_a": left,
                    "condition_b": right,
                    "matched_scored_pairs": len(keys),
                    "progress_delta_b_minus_a": mean(
                        [mean(group) for group in progress_by_task.values()]
                    ),
                    "progress_delta_ci95": bootstrap_hierarchical_ci(
                        list(progress_by_task.values()),
                        seed=1000 + index * 31 + names.index(right),
                    ),
                    "success_delta_b_minus_a": mean(
                        [mean(group) for group in success_by_task.values()]
                    ),
                    "success_delta_ci95": bootstrap_hierarchical_ci(
                        list(success_by_task.values()),
                        seed=2000 + index * 31 + names.index(right),
                    ),
                }
            )
    return {"conditions": condition_summary, "paired": paired}


matrices = []
for matrix_path in sorted((root / "runs").rglob("matrix.json")):
    try:
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    jobs = matrix.get("jobs") or []
    executed_jobs = [
        job
        for job in jobs
        if "returncode" in job
        or job.get("status")
        in {"completed", "process-failed", "launcher-error", "result-recovered"}
    ]
    matrices.append(
        {
            "path": str(matrix_path.relative_to(root)),
            "protocol": matrix.get("protocol"),
            "executed": bool(matrix.get("executed")),
            "jobs": len(jobs),
            "completed": len(executed_jobs),
            "attempts_total": sum(
                len(job.get("attempt_history", []))
                + (1 if job.get("started_at") else 0)
                for job in jobs
            ),
            "pending": sum(job.get("status", "pending") == "pending" for job in jobs),
            "running": sum(job.get("status") == "running" for job in jobs),
            "interrupted": sum(job.get("status") == "interrupted" for job in jobs),
            "failures": sum(
                job.get("status") in {"process-failed", "launcher-error"}
                or job.get("returncode", 0) != 0
                or (
                    job.get("status") == "result-recovered"
                    and job.get("scored") is not True
                )
                for job in executed_jobs
            ),
            "condition_summary": matrix_analysis(matrix_path),
        }
    )
audits = []
for audit_path in sorted((root / "reports/deployment/audits").glob("*.json")):
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    audits.append(
        {
            "path": str(audit_path.relative_to(root)),
            "protocol": audit.get("protocol"),
            "audit_type": audit.get("audit_type"),
            "model_calls": audit.get("model_calls"),
            "score_claims": audit.get("score_claims"),
            "rows": len(audit.get("rows") or []),
            "controls": [
                {
                    "name": row.get("control_type"),
                    "scored": row.get("scored"),
                    "success": row.get("success"),
                    "progress": row.get("progress"),
                }
                for row in audit.get("rows") or []
            ],
        }
    )
out = root / "reports/deployment"
out.mkdir(parents=True, exist_ok=True)
building_fixture_path = out / "building-demo-fixture-score.json"
building_fixture = (
    json.loads(building_fixture_path.read_text(encoding="utf-8"))
    if building_fixture_path.is_file()
    else None
)
payload = {
    "protocol": "benchmark-results-v2",
    "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    "runs": rows,
    "matrices": matrices,
    "audits": audits,
    "building_demo_fixture": building_fixture,
    "scope": "Diagnostic single-task trials; preserve failures; no aggregate across incompatible suites.",
}
(out / "results.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
)
lines = [
    "# 本地部署与真实评测报告",
    "",
    "更新时间：" + payload["updated_at"],
    "",
    "本报告汇总当前 checkout 的本地运行；各次运行的源码版本、配置和数据版本以其 manifest 与任务协议为准。",
    "支持固定版本 GameWorld（34 游戏 / 170 任务）、AI GameStore（10 游戏）与官方 ARC3 API；当前机器是否已部署须用 doctor 检查。",
    "模型与服务由每次运行的配置决定；实际返回模型标识、用量、耗时分别保留。",
    "",
    "| 运行 | 结果 | 评分 | 决策 / 动作 | 墙钟 | 模型调用；模型 p50/p95 | 动作 p50/p95；token |",
    "| --- | --- | --- | --- | --- | --- | --- |",
]
for row in rows:
    status = (
        "运行错误"
        if row["error"]
        else "成功"
        if row["success"] is True
        else ("有效评分，未通关" if row["success"] is False else "有效评分")
        if row["scored"]
        else "未完成"
    )
    score = (
        f"任务完成度 {row['progress']:.0%}"
        if row["progress"] is not None
        else f"ARC 官方 {row['official_score']}"
        if row["official_score"] is not None
        else f"GameStore 原始 {row['raw_score']}"
        if row["raw_score"] is not None and row["scored"]
        else "不计分"
    )
    run = row["run"]
    link = f"[{run}](../../runs/{run}/result.json)"
    seconds = (
        f"{row['elapsed_seconds']:.1f}s" if row["elapsed_seconds"] is not None else "—"
    )
    model_latency = (
        f"{row['model_latency_p50']:.2f}/{row['model_latency_p95']:.2f}s"
        if row.get("model_latency_p50") is not None
        and row.get("model_latency_p95") is not None
        else "—"
    )
    action_latency = (
        f"{row['action_latency_p50']:.2f}/{row['action_latency_p95']:.2f}s"
        if row.get("action_latency_p50") is not None
        and row.get("action_latency_p95") is not None
        else "—"
    )
    tokens = (
        row.get("prompt_tokens") + row.get("completion_tokens")
        if isinstance(row.get("prompt_tokens"), (int, float))
        and isinstance(row.get("completion_tokens"), (int, float))
        else "—"
    )
    steps = row["steps"]
    step_label = "决策" if row["step_kind"] == "decision" else "动作"
    step_text = f"{steps} {step_label}"
    if row["executed_actions"] is not None:
        step_text += (
            f"；执行 {row['executed_actions']} 动作；仅文字 {row['text_only_turns']} 轮"
        )
    lines.append(
        f"| {link} | {status} | {score} | {step_text} | {seconds} | {row['model_calls']}；{model_latency} | {action_latency}；{tokens} |"
    )
by_name = {row["run"]: row for row in rows}
lines += ["", "## 已完成的对比", ""]
mc_baseline = by_name.get("minecraft-clone-dirt-baseline")
mc_vista = by_name.get("minecraft-clone-dirt")
if mc_baseline and mc_vista:
    lines += [
        f"MC 风格采集 `18_01`：基础提示新增泥土 17/20，完成度 {mc_baseline['progress']:.0%}，100 次决策（实际执行 {mc_baseline['executed_actions']} 次动作）；增强提示新增泥土 20/20，完成度 {mc_vista['progress']:.0%}，50 次决策（实际执行 {mc_vista['executed_actions']} 次动作）并成功。任务完成度增加 15 个百分点。",
        "两次均为 `deepseek-flash`、seed=42、关闭思考、最多 110 次模型调用、每次最多 2048 输出 token、900 秒会话预算，沿用上游 100 次决策上限，成功后提前结束。早期增强运行的 manifest 未包含 thinking 字段，但其归档请求明确发送了 `thinking.type=disabled`。",
        "采集数量由游戏评估器按本轮新增量核定；初始背包中的泥土不计入目标。",
        "基础运行有 49 轮只回复文字，反复声称采集完成，实际未达标。优先改进可见背包计数和未终止时的行动策略。此前报告曾将 100 次决策误标为 100 次动作，现已按轨迹更正；原始分数及日志未改动。",
        "",
    ]
baseline = by_name.get("gameworld-2048-baseline")
vista = by_name.get("gameworld-2048-smoke")
if baseline and vista:
    reduction = 1 - vista["steps"][0] / baseline["steps"][0]
    faster = 1 - vista["elapsed_seconds"] / baseline["elapsed_seconds"]
    lines += [
        f"2048 `01_01`、seed=42、同一模型和 API/时间预算：基础与增强提示均成功，完成度均为 100%。增强提示动作减少 {reduction:.1%}，本次墙钟用时减少 {faster:.1%}。",
        "",
    ]
arc_thinking = by_name.get("arc3-vc33-thinking")
if arc_thinking:
    lines += [
        f"ARC3 `vc33`：非思考基础/增强提示均为官方分数 0、0/7 关；开启思考的独立运行获得官方分数 {arc_thinking['official_score']:.6f}，通过 {arc_thinking['levels_completed']}/{arc_thinking['total_levels']} 关，30 次动作，约 601 秒，scorecard 已关闭。仍未通关。",
        "思考运行同时把输出上限从 1600 改为 8192 token、模型调用上限从 80 改为 60、动作上限从 60 改为 80；两组会话预算均为 600 秒。分数直接保留官方返回值，不解释为百分比或整体通过率。该结果不构成只改变思考模式的公平对照。",
        "",
    ]
lines += [
    "以上每种配置各运行一次，属于诊断结果。模型随机性、服务延迟与运行时负载会影响表现；尚未完成多种子重复试验，不能推断稳定优势。"
]
reliability_rows = [row for row in rows if row.get("reliability")]
if reliability_rows:
    lines += [
        "",
        "## 过程可靠性诊断",
        "",
        "新增诊断字段只使用 Agent 可见截图、公开工具回执和 Agent 自己提交的状态卡；`evidence_semantic_accuracy`、`state_accuracy`、`recovery_success_rate` 和 `strict_success` 在没有独立标注或任务约束时保留为 null，不计入原生分数。",
        "",
        "| 运行 | 状态卡 | 证据引用覆盖 | 合法动作率 | 非法动作率 | 纯文字轮次 | 恢复 | 检查 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in reliability_rows:
        diag = row["reliability"]
        coverage = (
            f"{diag['evidence_reference_coverage']:.0%}"
            if diag.get("evidence_reference_coverage") is not None
            else "—"
        )
        validity = (
            f"{diag['action_validity']:.0%}"
            if diag.get("action_validity") is not None
            else "—"
        )
        invalid_rate = (
            f"{diag['invalid_action_rate']:.0%}"
            if diag.get("invalid_action_rate") is not None
            else "—"
        )
        lines.append(
            f"| {row['run']} | {diag['state_cards']} | {coverage} | {validity} | {invalid_rate} | {diag['text_only_turns']} | {diag['recovery_interventions']} | {diag['inspection_calls']} |"
        )
if matrices:
    lines += [
        "",
        "## 多任务矩阵",
        "",
        "矩阵只统计已声明的任务、种子和配置；计划模式不会调用模型，也不产生有效评分。",
        "",
        "| 计划 | 声明 job | 已结束 | 启动次数 | 待执行 | 中断 | 失败 | 模式 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for matrix in matrices:
        lines.append(
            f"| `{matrix['path']}` | {matrix['jobs']} | {matrix['completed']} | {matrix['attempts_total']} | {matrix['pending']} | {matrix['interrupted']} | {matrix['failures']} | {'执行' if matrix['executed'] else '仅计划'} |"
        )
        condition_rows = matrix.get("condition_summary", {}).get("conditions", [])
        paired_rows = matrix.get("condition_summary", {}).get("paired", [])
        if condition_rows:
            lines += [
                "",
                f"### `{matrix['path']}` 条件统计",
                "",
                "成功率和进度只在 `scored=true` 的运行中计算；未评分运行保留在 attempted/unscored 分母。成本列是每次运行均值，不与成功率压成单一总分。",
                "",
                "| 条件 | 已评分/已尝试 | 成功率 | 进度均值±SD | 决策均值 | 动作均值 | 平均秒 | 平均调用 | 模型 p95 | 动作 p95 | 平均 token |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
            for item in condition_rows:
                rate = (
                    f"{item['success_rate']:.1%}"
                    if item.get("success_rate") is not None
                    else "—"
                )
                progress = (
                    f"{item['progress_mean']:.1%}"
                    + (
                        f" ± {item['progress_sd']:.1%}"
                        if item.get("progress_sd") is not None
                        else ""
                    )
                    if item.get("progress_mean") is not None
                    else "—"
                )
                def number(value, digits=1):
                    return f"{value:.{digits}f}" if value is not None else "—"

                lines.append(
                    f"| {item['condition']} | {item['scored']}/{item['attempted']} | {rate} | {progress} | {number(item.get('decisions_mean'))} | {number(item.get('actions_mean'))} | {number(item.get('elapsed_seconds_mean'))} | {number(item.get('model_calls_mean'))} | {number(item.get('model_latency_p95_mean'), 2)}s | {number(item.get('action_latency_p95_mean'), 2)}s | {number(item.get('tokens_mean'), 0)} |"
                )
        if paired_rows:
            lines += [
                "",
                "配对差值按相同游戏/任务/种子匹配，先在任务内平均、再对任务与任务内种子做两级 bootstrap；方向为 B − A。任务族少于 2 个时不计算区间；其余小样本区间仅作探索性描述，不代表显著性检验。",
                "",
                "| 条件 A | 条件 B | 配对数 | 进度差 B−A [95% CI] | 成功率差 B−A [95% CI] |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
            for item in paired_rows:
                def paired_value(value, interval, percent=True):
                    if value is None:
                        return "—"
                    scale = 100 if percent else 1
                    rendered = f"{value * scale:+.1f}{'pp' if percent else ''}"
                    if interval:
                        rendered += (
                            f" [{interval[0] * scale:+.1f}, {interval[1] * scale:+.1f}]"
                            + (" pp" if percent else "")
                        )
                    return rendered

                lines.append(
                    f"| {item['condition_a']} | {item['condition_b']} | {item['matched_scored_pairs']} | {paired_value(item.get('progress_delta_b_minus_a'), item.get('progress_delta_ci95'))} | {paired_value(item.get('success_delta_b_minus_a'), item.get('success_delta_ci95'))} |"
                )
if audits:
    lines += [
        "",
        "## Benchmark 有效性审计",
        "",
        "控制器使用公开动作空间和固定随机种子，不调用模型、不读取 evaluator；分数仍由原生评估器独立产生。",
        "",
        "| 审计 | 类型 | 模型调用 | 记录条目 | 控制结果 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for audit in audits:
        controls = (
            ", ".join(
                f"{control['name']} {control['progress']:.0%}"
                for control in audit["controls"]
                if control["name"] and isinstance(control.get("progress"), (int, float))
            )
            or "—"
        )
        lines.append(
            f"| `{audit['path']}` | {audit['audit_type']} | {audit['model_calls']} | {audit['rows']} | {controls} |"
        )
if building_fixture:
    score = building_fixture["score"]
    lines += [
        "",
        "## 建筑评分器 fixture",
        "",
        f"固定样例检查：方块位置完成度 {score['completion']:.0%}、材料准确率 {score['material_accuracy']:.0%}、位置准确率 {score['position_accuracy']:.0%}。这是评分器 fixture，不是 Agent 在游戏中的建造成绩。",
    ]
lines += [
    "",
    "## 评分边界",
    "",
    "- 旧 DeepSeek 适配按回复关键字产生的 12/100、48/100 不构成有效基线，未纳入对比。",
    "- MC 风格沙盒只验证采集任务；不是正式 Minecraft 的建筑验收或击败末影龙。饥荒与泰拉瑞亚仍需独立部署。",
    "- ARC3 保留官方部分分数及未通关状态，不将 1/7 关计作整场成功。",
    "- GameStore 射击运行只使用 5 次决策，用于验证动作和计分闭环，不与完整 120 决策结果比较。",
    "- 所有 `scored=false` 的运行单列，不剔除后声称全套通过率；没有生成跨套件的虚构综合分。",
    "- 回放只浏览保存的画面和日志，不声称游戏引擎可以完全确定性重演。",
    "",
    "## 使用与证据",
    "",
    "打开 [本地结果页面](http://localhost:8765)。如果服务已关闭，双击根目录的 `打开Benchmark.cmd`。",
    "每个已结束运行目录含 `result.json`、`replay.html`，模型请求/响应与动作日志保留在其 transport 或 private/api 子目录。",
    "模型请求没有写入认证 header。源码和数据版本、输入配置与源码哈希保存在 manifest。",
    "[使用说明](../../README.md) · [机器可读结果](results.json) · [安装检查](installed/doctor.json)",
    "",
]
(out / "部署与评测报告.md").write_text("\n".join(lines), encoding="utf-8")
print(
    json.dumps(
        {"runs": len(rows), "report": str(out / "部署与评测报告.md")},
        ensure_ascii=False,
    )
)
