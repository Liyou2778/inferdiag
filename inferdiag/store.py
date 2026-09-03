"""SQLite 时序存储（v0 极简实现，schema 演进见架构文档）。

表 samples(ts REAL, engine TEXT, payload TEXT)：payload 为 Sample 的 JSON。
window_metrics() 把最近一个时间窗聚合成规则引擎需要的指标字典。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .collector.models import Sample


class SQLiteStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._init()

    def _init(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
                ts      REAL NOT NULL,
                engine  TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
        self._conn.commit()

    def insert_sample(self, sample: Sample) -> None:
        self._conn.execute(
            "INSERT INTO samples (ts, engine, payload) VALUES (?, ?, ?)",
            (sample.ts, sample.engine, json.dumps(sample.to_dict())),
        )
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM samples").fetchone()
        return int(row[0]) if row else 0

    def latest(self, n: int = 10) -> list[Sample]:
        rows = self._conn.execute(
            "SELECT payload FROM samples ORDER BY ts DESC LIMIT ?", (n,)
        ).fetchall()
        return [Sample(**json.loads(r[0])) for r in rows]

    def window_samples(self, seconds: float = 60.0) -> list[Sample]:
        """返回最近 seconds 秒内、按时间正序的样本。"""
        cutoff = time.time() - seconds
        rows = self._conn.execute(
            "SELECT payload FROM samples WHERE ts >= ? ORDER BY ts ASC", (cutoff,)
        ).fetchall()
        return [Sample(**json.loads(r[0])) for r in rows]

    def window_metrics(self, seconds: float = 60.0) -> dict:
        """把时间窗样本聚合成规则引擎指标字典。

        计数型指标(带 _total/_rate)按窗口首末差值算速率；量规型取窗口 max/mean。
        样本不足 2 条时返回带 sample_count 的空指标（规则全部跳过）。
        """
        samples = self.window_samples(seconds)
        out: dict = {"sample_count": len(samples)}
        if len(samples) < 2:
            return out

        dt = max(1e-6, samples[-1].ts - samples[0].ts)
        out["window_seconds"] = round(dt, 1)

        def max_of(attr: str):
            vals = [getattr(s, attr) for s in samples if getattr(s, attr) is not None]
            return max(vals) if vals else None

        def mean_of(attr: str):
            vals = [getattr(s, attr) for s in samples if getattr(s, attr) is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        def rate_of(attr: str):
            vals = [getattr(s, attr) for s in samples if getattr(s, attr) is not None]
            if not vals:
                return None
            delta = vals[-1] - vals[0]
            return round(max(0.0, delta) / dt, 4)

        out["num_running"] = max_of("num_running")
        out["num_waiting"] = max_of("num_waiting")
        out["kv_cache_usage_pct"] = max_of("kv_cache_usage_pct")
        out["prefix_cache_hit_pct"] = mean_of("prefix_cache_hit_pct")
        out["ttft_p50_ms"] = mean_of("ttft_p50_ms")
        out["ttft_p99_ms"] = mean_of("ttft_p99_ms")
        out["tpot_ms"] = mean_of("tpot_ms")
        out["e2e_p50_ms"] = mean_of("e2e_p50_ms")
        out["e2e_p99_ms"] = mean_of("e2e_p99_ms")
        out["preemptions_rate"] = rate_of("preemptions_total")
        out["requests_success_rate"] = rate_of("requests_success_total")
        out["prompt_tokens_rate"] = rate_of("prompt_tokens_total")
        out["generation_tokens_rate"] = rate_of("generation_tokens_total")
        # 前缀缓存命中率 = 窗口内 hits/queries 增量之比
        hits = rate_of("prefix_cache_hits_total")
        queries = rate_of("prefix_cache_queries_total")
        if hits is not None and queries and queries > 0:
            out["prefix_cache_hit_pct"] = round(min(100.0, hits / queries * 100), 1)
        out["cost_per_mtok"] = None  # 由 cost 模块计算后填充
        return out

    def close(self) -> None:
        self._conn.close()
