"""归一化后的指标样本模型。

v0 阶段存储为 JSON 快照；字段名遵循采集清单文档（docs/architecture.md 第 6 节）。
真实引擎指标名以各版本实际输出为准，缺失字段保留 None。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field


@dataclass
class Sample:
    """一次抓取得到的引擎健康快照。"""

    ts: float = field(default_factory=time.time)  # 采集时间戳
    engine: str = "vllm"  # vllm | sglang | mock
    # 服务健康
    num_running: float | None = None
    num_waiting: float | None = None
    num_swapped: float | None = None
    preemptions_total: float | None = None
    requests_success_total: float | None = None
    # 延迟（毫秒，秒值×1000 后存储）
    ttft_p50_ms: float | None = None
    ttft_p99_ms: float | None = None
    tpot_ms: float | None = None
    e2e_p50_ms: float | None = None
    e2e_p99_ms: float | None = None
    # token 计数
    prompt_tokens_total: float | None = None
    generation_tokens_total: float | None = None
    # KV cache / 显存
    kv_cache_usage_pct: float | None = None
    cpu_cache_usage_pct: float | None = None
    prefix_cache_hit_pct: float | None = None
    # GPU（来自 nvidia-smi/DCGM 时填充）
    gpu_util_pct: float | None = None
    gpu_mem_used_mib: float | None = None
    gpu_mem_total_mib: float | None = None
    # 附加元信息
    raw_series_count: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        """一行人类可读摘要，用于终端演示。"""
        return (
            f"engine={self.engine} running={self.num_running} waiting={self.num_waiting} "
            f"kv_cache={self.kv_cache_usage_pct}% ttft_p50={self.ttft_p50_ms}ms "
            f"tok_prompt={self.prompt_tokens_total} tok_gen={self.generation_tokens_total}"
        )
