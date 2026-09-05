"""窗口聚合 idle 判定与 R10 尾巴误报修复的测试。"""

import tempfile
import time
from pathlib import Path

import pytest

from inferdiag.collector.models import Sample
from inferdiag.rules.engine import evaluate
from inferdiag.store import SQLiteStore


def _sample(ts: float, running: float = 0.0, kv: float = 0.0, success: float = 0.0) -> Sample:
    return Sample(
        ts=ts, engine="vllm", num_running=running, num_waiting=0.0,
        kv_cache_usage_pct=kv, requests_success_total=success,
    )


def _store_with(samples: list[Sample]) -> SQLiteStore:
    d = tempfile.mkdtemp()
    store = SQLiteStore(str(Path(d) / "t.db"))
    for s in samples:
        store.insert_sample(s)
    return store


def _ids(metrics: dict) -> set:
    return {f["rule_id"] for f in evaluate(metrics)}


def test_pure_idle_triggers_r10():
    """整窗都在空转 => idle_no_activity=True => R10 应触发。"""
    now = time.time()
    store = _store_with([_sample(now - 4, 0, 0, 0), _sample(now - 2, 0, 0, 0), _sample(now, 0, 0, 0)])
    try:
        m = store.window_metrics(60)
        assert m["idle_no_activity"] is True
        assert "R10" in _ids(m)
    finally:
        store.close()


def test_load_then_idle_tail_does_not_trigger_r10():
    """窗口前一半有负载、后一半空闲（回落后段）=> idle_no_activity=False => R10 不触发。"""
    now = time.time()
    samples = [_sample(now - 10, 3.0, 3.0, 2.0), _sample(now - 7, 2.0, 2.0, 4.0),
               _sample(now - 4, 0, 0, 4.0), _sample(now - 2, 0, 0, 4.0), _sample(now, 0, 0, 4.0)]
    store = _store_with(samples)
    try:
        m = store.window_metrics(60)
        assert m["idle_no_activity"] is False
        assert "R10" not in _ids(m)
    finally:
        store.close()


def test_engine_r10_requires_flag_metric():
    """规则层面：即使 kv/running 都低，缺 idle_no_activity 标记时也不触发 R10。"""
    m = {"kv_cache_usage_pct": 0.0, "num_running": 0.0}
    assert "R10" not in _ids(m)
    m2 = {"kv_cache_usage_pct": 0.0, "num_running": 0.0, "idle_no_activity": True}
    assert "R10" in _ids(m2)
    m3 = {"kv_cache_usage_pct": 0.0, "num_running": 0.0, "idle_no_activity": False}
    assert "R10" not in _ids(m3)


def test_purge_before_removes_old_samples():
    now = time.time()
    store = _store_with([_sample(now - 100, 1, 1), _sample(now - 50, 1, 1), _sample(now - 10, 1, 1)])
    try:
        removed = store.purge_before(now - 30)
        assert removed == 2
        assert len(store.latest(10)) == 1
        assert store.latest(1)[0].ts == pytest.approx(now - 10, abs=2)
    finally:
        store.close()
