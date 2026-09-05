"""成本估算。

单价（每百万 token，人民币）默认示例值，可通过环境变量覆盖：
    INFERDIAG_PRICE_IN / INFERDIAG_PRICE_OUT
"""

from __future__ import annotations

import os
from typing import Any


def _pricing_from_env() -> dict[str, float]:
    in_price = os.environ.get("INFERDIAG_PRICE_IN")
    out_price = os.environ.get("INFERDIAG_PRICE_OUT")
    if in_price is not None and out_price is not None:
        return {"input_per_mtok": float(in_price), "output_per_mtok": float(out_price)}
    return {"input_per_mtok": 1.0, "output_per_mtok": 3.0}


PRICING: dict[str, float] = _pricing_from_env()


def estimate_cost(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """按窗口内 token 增量估算成本。无 token 数据时返回 None。"""
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
        "note": "单价来自 cost.PRICING（默认示例值，可用环境变量覆盖）。",
    }
