"""成本估算（v0 极简版）。

单价表未配置时返回 None，规则 R8 自动跳过；配置方式：后续引入 config 文件/CLI 参数。
"""

from __future__ import annotations

from typing import Any

# 单价（每百万 token，人民币）：开箱即用的示例值，接入真实部署时按实际账单修改
PRICING: dict[str, float] | None = {"input_per_mtok": 1.0, "output_per_mtok": 3.0}


def estimate_cost(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """按窗口内 token 增量估算成本。无单价表或无 token 数据时返回 None。"""
    if PRICING is None:
        return None
    prompt_rate = metrics.get("prompt_tokens_rate") or 0.0
    gen_rate = metrics.get("generation_tokens_rate") or 0.0
    window = metrics.get("window_seconds") or 1.0
    prompt_toks = prompt_rate * window
    gen_toks = gen_rate * window
    cost = (
        prompt_toks / 1e6 * PRICING["input_per_mtok"]
        + gen_toks / 1e6 * PRICING["output_per_mtok"]
    )
    return {
        "window_tokens_in": round(prompt_toks),
        "window_tokens_out": round(gen_toks),
        "window_cost_rmb": round(cost, 4),
        "note": "单价为示例配置，请按实际账单修改 PRICING。",
    }
