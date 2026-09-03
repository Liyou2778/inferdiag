"""Prometheus 文本格式解析与字段归一化。

引擎只读依赖：vLLM / SGLang 均暴露 Prometheus 文本格式的 /metrics。
本模块把文本解析成 Sample；命名差异在此层统一。

字段名以你实际抓到的 /metrics 为准 —— 抓到什么就映射什么，抓不到就留 None。
匹配策略：不依赖 family 名（不同解析器对 counter 的 `_total` 处理不一致），
而是直接扫描全部样本的 name 做"裸名匹配"，对 gauge/counter/summary 都稳。
"""

from __future__ import annotations

from prometheus_client.parser import text_string_to_metric_families

from .models import Sample


def parse_prometheus_text(text: str) -> dict[str, list[dict]]:
    """把 Prometheus 文本解析为 {metric_family_name: [sample,...]}。

    每个 sample: {"name": str, "labels": dict, "value": float}
    """
    out: dict[str, list[dict]] = {}
    for family in text_string_to_metric_families(text):
        samples = []
        for s in family.samples:
            samples.append(
                {
                    "name": s.name,
                    "labels": dict(s.labels or {}),
                    "value": float(s.value),
                }
            )
        out[family.name] = samples
    return out


def _all_samples(snapshot: dict[str, list[dict]]) -> list[dict]:
    """展平为所有样本的列表。"""
    return [s for samples in snapshot.values() for s in samples]


def _bare(name: str) -> str:
    """去掉引擎前缀（vllm:/sglang:），取裸指标名。"""
    return name.split(":", 1)[-1]


def _pick(snapshot: dict[str, list[dict]], target: str) -> float | None:
    """取与 target 裸名一致的最后一个样本值。"""
    matched = [s for s in _all_samples(snapshot) if _bare(s["name"]) == target]
    if not matched:
        return None
    # 优先取无标签样本
    for s in reversed(matched):
        if not s["labels"]:
            return s["value"]
    return matched[-1]["value"]


def _quantile(snapshot: dict[str, list[dict]], target: str, q: str) -> float | None:
    """取裸名为 target、且带 quantile=q 标签的样本值（Summary 风格）。"""
    for s in _all_samples(snapshot):
        if _bare(s["name"]) == target and s["labels"].get("quantile") == q:
            return s["value"]
    return None


def _histogram_quantile(
    snapshot: dict[str, list[dict]], target: str, q: float
) -> float | None:
    """从 histogram 的 _bucket 样本估算分位数（vLLM 用 histogram 而非 summary）。

    返回分位数所在 bucket 的上界（+Inf 视为总数）。vLLM 的 bucket 较粗，
    v0 用"上界近似"足够做趋势判断。
    """
    buckets: list[tuple[float, float]] = []  # (le, cumulative_count)
    total: float | None = None
    for s in _all_samples(snapshot):
        if _bare(s["name"]) != f"{target}_bucket":
            continue
        le = s["labels"].get("le")
        if le is None:
            continue
        if le == "+Inf":
            total = s["value"]
        else:
            buckets.append((float(le), s["value"]))
    if total is None:
        total = buckets[-1][1] if buckets else None
    if not buckets or not total or total <= 0:
        return None
    buckets.sort()
    target_count = q * total
    for le, cum in buckets:
        if cum >= target_count:
            return le
    return buckets[-1][0]


def normalize(snapshot: dict[str, list[dict]], engine: str = "auto") -> Sample:
    """把 parse 结果归一化到 Sample。"""

    if engine == "auto":
        names = [_bare(s["name"]) for s in _all_samples(snapshot)]
        text = " ".join(names)
        if "num_requests_running" in text and any(n.startswith(("sglang", "sgl")) for n in names):
            engine = "sglang"
        elif any(n.startswith("vllm") for n in names) or "num_requests_running" in text:
            engine = "vllm"
        else:
            engine = "unknown"

    def sec_to_ms(v: float | None) -> float | None:
        return None if v is None else round(v * 1000, 2)

    def latency_ms(target: str, q_label: str, q_num: float) -> float | None:
        """Summary 标签优先，histogram 估算兜底。"""
        v = _quantile(snapshot, target, q_label)
        if v is None:
            v = _histogram_quantile(snapshot, target, q_num)
        return sec_to_ms(v)

    s = Sample(engine=engine)
    s.num_running = _pick(snapshot, "num_requests_running")
    s.num_waiting = _pick(snapshot, "num_requests_waiting")
    s.num_swapped = _pick(snapshot, "num_requests_swapped")
    s.preemptions_total = _pick(snapshot, "num_preemptions_total")
    s.requests_success_total = _pick(snapshot, "request_success_total")
    s.ttft_p50_ms = latency_ms("time_to_first_token_seconds", "0.5", 0.5)
    s.ttft_p99_ms = latency_ms("time_to_first_token_seconds", "0.99", 0.99)
    tpot = _pick(snapshot, "time_per_output_token_seconds")
    if tpot is None:
        tpot = _histogram_quantile(snapshot, "time_per_output_token_seconds", 0.5)
    s.tpot_ms = None if tpot is None else round(tpot * 1000, 2)
    s.e2e_p50_ms = latency_ms("e2e_request_latency_seconds", "0.5", 0.5)
    s.e2e_p99_ms = latency_ms("e2e_request_latency_seconds", "0.99", 0.99)
    s.prompt_tokens_total = _pick(snapshot, "prompt_tokens_total")
    s.generation_tokens_total = _pick(snapshot, "generation_tokens_total")
    kv = _pick(snapshot, "gpu_cache_usage_perc")
    if kv is None:
        kv = _pick(snapshot, "kv_cache_usage_perc")
    s.kv_cache_usage_pct = None if kv is None else round(kv * 100, 1)
    s.cpu_cache_usage_pct = _pick(snapshot, "cpu_cache_usage_perc")
    s.prefix_cache_hits_total = _pick(snapshot, "gpu_prefix_cache_hits_total")
    s.prefix_cache_queries_total = _pick(snapshot, "gpu_prefix_cache_queries_total")
    s.raw_series_count = sum(len(v) for v in snapshot.values())
    return s
