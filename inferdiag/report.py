"""报告生成：健康分 + 体检结果 + 成本估算（人类可读版在 CLI 里渲染）。"""

from __future__ import annotations

import time
from typing import Any

from .rules.engine import evaluate


def health_score(findings: list[dict]) -> int:
    """健康分：100 起，critical -25 / warning -10 / info -3，最低 0。"""
    weights = {"critical": 25, "warning": 10, "info": 3}
    penalty = sum(weights.get(f["level"], 0) for f in findings)
    return max(0, 100 - penalty)


def build_report(
    metrics: dict[str, Any],
    window_seconds: float,
    cost_info: dict[str, Any] | None = None,
) -> dict:
    findings = evaluate(metrics)
    score = health_score(findings)
    return {
        "generated_at": time.time(),
        "window_seconds": window_seconds,
        "score": score,
        "sample_count": metrics.get("sample_count", 0),
        "metrics_snapshot": {k: v for k, v in metrics.items() if k != "sample_count"},
        "findings": findings,
        "cost": cost_info,
        "notes": [
            "阈值为 RTX 4060 + Qwen2.5-3B-AWQ 的基线标定值；更换硬件/模型/业务形态后请重新校准（见 docs/calibration.md）。",
        ],
    }
