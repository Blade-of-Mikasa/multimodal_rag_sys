"""Human-reviewable Chinese report rendering with optional baseline deltas."""

from __future__ import annotations

from typing import Any


_METRIC_LABELS = {
    "planner_required_recall": "召回规划覆盖率",
    "unnecessary_route_rate": "无效召回率",
    "required_evidence_recall": "必要证据覆盖率",
    "valid_evidence_deletion_rate": "有效证据误删率",
    "conflict_detection_recall": "冲突发现率",
    "answer_correctness": "最终答案正确率",
    "answer_completeness": "答案完整率（诊断项）",
    "evidence_faithfulness": "证据忠实度",
    "mean_estimated_cost_usd": "单请求平均估算成本",
}


def render_markdown(
    summary: dict[str, Any], *, baseline: dict[str, Any] | None = None
) -> str:
    lines = [
        f"# RAG 分阶段评估报告：{summary['run_id']}",
        "",
        f"- 生成时间：`{summary['generated_at']}`",
        f"- 样本数：{summary['case_count']}",
        f"- 契约版本：`{summary['schema_version']}`",
        f"- 输入 SHA-256：`{summary['provenance']['source_sha256'] or '未提供'}`",
        "",
        "## 核心效果与成本",
        "",
        "| 指标 | 当前值 | 分子/分母 | 相对基线 |",
        "|---|---:|---:|---:|",
    ]
    for name, metric in summary["metrics"].items():
        value = metric["value"]
        baseline_value = _baseline_metric(baseline, name)
        lines.append(
            "| "
            + _METRIC_LABELS.get(name, name)
            + " | "
            + _format_metric(value, metric["unit"])
            + f" | {metric['numerator']:.6g}/{metric['denominator']:.6g} | "
            + _format_delta(value, baseline_value, metric["unit"])
            + " |"
        )
    latency = summary["latency_percentiles_ms"]
    lines.extend(
        [
            "",
            "## 延迟与用量",
            "",
            f"- 端到端延迟：P50 {latency['p50']:.2f} ms / "
            f"P95 {latency['p95']:.2f} ms / P99 {latency['p99']:.2f} ms",
            f"- Token：输入 {summary['usage']['input_tokens']} / "
            f"输出 {summary['usage']['output_tokens']}",
            "- 总估算成本："
            f"${summary['usage']['estimated_cost_usd']:.6f}",
            "",
            "### 分阶段 P95",
            "",
            "| 阶段 | P95 延迟 |",
            "|---|---:|",
        ]
    )
    if summary["stage_p95_latency_ms"]:
        lines.extend(
            f"| `{stage}` | {duration:.2f} ms |"
            for stage, duration in summary["stage_p95_latency_ms"].items()
        )
    else:
        lines.append("| - | 无阶段数据 |")
    lines.extend(["", "## 失败分布", ""])
    if summary["error_counts"]:
        lines.extend(
            f"- `{code}`：{count}"
            for code, count in summary["error_counts"].items()
        )
    else:
        lines.append("- 本次评估没有记录终态错误。")
    lines.extend(
        [
            "",
            "> 本报告不计算统一 RAG 总分；"
            "优先用分阶段指标定位瓶颈。",
            "",
        ]
    )
    return "\n".join(lines)


def _baseline_metric(
    baseline: dict[str, Any] | None, name: str
) -> float | None:
    if baseline is None:
        return None
    value = baseline.get("metrics", {}).get(name, {}).get("value")
    return value if isinstance(value, (int, float)) else None


def _format_metric(value: float | None, unit: str) -> str:
    if value is None:
        return "N/A"
    if unit == "ratio":
        return f"{value:.2%}"
    if unit == "usd":
        return f"${value:.6f}"
    return f"{value:.6g}"


def _format_delta(
    value: float | None, baseline: float | None, unit: str
) -> str:
    if value is None or baseline is None:
        return "-"
    delta = value - baseline
    if unit == "ratio":
        return f"{delta:+.2%}"
    if unit == "usd":
        return f"${delta:+.6f}"
    return f"{delta:+.6g}"
