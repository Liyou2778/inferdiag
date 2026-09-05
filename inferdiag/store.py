"""SQLite 时序存储。

表 samples(ts REAL, engine TEXT, payload TEXT)：payload 为 Sample 的 JSON。
window_metrics() 把最近一个时间窗聚合成规则引擎需要的指标字典。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .collector.models import Sample


class SQLiteStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：FastAPI/uvicorn 在工作线程执行请求，
        # 而连接在主线程创建。读写通过 self._lock 串行化。
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=15)
        self._lock = threading.RLock()
        self._init()

    def _init(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=8000")
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
        with self._lock:
            self._conn.execute(
                "INSERT INTO samples (ts, engine, payload) VALUES (?, ?, ?)",
                (sample.ts, sample.engine, json.dumps(sample.to_dict())),
            )
            self._conn.commit()

    def count(self) -> int:
        """样本行数（O(1)，用最大 rowid 近似；执行过 purge 后为近似值）。"""
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM samples").fetchone()
            return int(row[0]) if row else 0

    def latest(self, n: int = 10) -> list[Sample]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM samples ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
        return [Sample(**json.loads(r[0])) for r in rows]

    def window_samples(self, seconds: float = 60.0) -> list[Sample]:
        """返回最近 seconds 秒内、按时间正序的样本。"""
        with self._lock:
            cutoff = time.time() - seconds
            rows = self._conn.execute(
                "SELECT payload FROM samples WHERE ts >= ? ORDER BY ts ASC", (cutoff,)
            ).fetchall()
        return [Sample(**json.loads(r[0])) for r in rows]

    def purge_before(self, cutoff_ts: float) -> int:
        """删除早于 cutoff_ts 的样本，返回删除行数。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff_ts,))
            self._conn.commit()
            return cur.rowcount

    def window_metrics(self, seconds: float = 60.0) -> dict:
        """把时间窗样本聚合成规则引擎指标字典。

        计数型指标(带 _total/_rate)按窗口首末差值算速率；量规型取窗口 max/mean。
        若时间窗内样本不足，自动回退到"最近的样本"（监控有空窗期是常态，不应报 0）。
        样本不足 2 条时返回带 sample_count 的空指标（规则全部跳过）。
        """
        samples = self.window_samples(seconds)
        fallback = len(samples) < 2
        if fallback:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT payload FROM samples ORDER BY ts DESC LIMIT 30"
                ).fetchall()
            samples = [Sample(**json.loads(r[0])) for r in reversed(rows)]
        out: dict = {"sample_count": len(samples), "used_fallback_window": fallback}
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
        # 每百万 token 混合成本（用于 R8），由单价表计算
        out["cost_per_mtok"] = None
        try:
            from .cost import PRICING

            if PRICING:
                in_r = out.get("prompt_tokens_rate") or 0.0
                out_r = out.get("generation_tokens_rate") or 0.0
                win = out.get("window_seconds") or 0.0
                total_tok = (in_r + out_r) * win
                cost = (
                    in_r * win / 1e6 * PRICING["input_per_mtok"]
                    + out_r * win / 1e6 * PRICING["output_per_mtok"]
                )
                if total_tok > 0:
                    out["cost_per_mtok"] = round(cost / total_tok * 1e6, 4)
        except Exception:  # noqa: BLE001, S110 单价缺失/异常不阻塞诊断
            pass
        # 子窗口判定：区分"本就在空转"与"负载刚结束的回落后段"（消除 R10 尾巴误报）
        mid = len(samples) // 2
        first = samples[: max(1, mid)]
        first_active = any(
            (s.num_running or 0) > 0
            or (s.num_waiting or 0) > 0
            or (s.kv_cache_usage_pct or 0) > 0.5
            for s in first
        )
        kv_max = out.get("kv_cache_usage_pct") or 0
        running_max = out.get("num_running") or 0
        idle_no_activity = (
            (not first_active)
            and not ((out.get("requests_success_rate") or 0) > 0)
            and kv_max <= 2
            and running_max <= 1
        )
        out["idle_no_activity"] = idle_no_activity
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()
