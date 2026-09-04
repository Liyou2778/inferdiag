"""Web API 测试（用内存临时库）。"""

import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from inferdiag.collector.models import Sample
from inferdiag.store import SQLiteStore
from inferdiag.web.app import create_app


def _make_sample(**kw) -> Sample:
    base = dict(
        ts=time.time(),
        engine="vllm",
        num_running=3.0,
        num_waiting=0.0,
        kv_cache_usage_pct=2.8,
        ttft_p50_ms=40.0,
        ttft_p99_ms=2500.0,
        e2e_p99_ms=15000.0,
        generation_tokens_total=1000.0,
    )
    base.update(kw)
    return Sample(**base)


def test_overview_and_series():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        store = SQLiteStore(db)
        for i in range(5):
            s = _make_sample(ts=time.time() - (5 - i) * 2)
            store.insert_sample(s)
        store.close()

        app = create_app(db)
        client = TestClient(app)
        try:
            r = client.get("/")
            assert r.status_code == 200
            assert "inferdiag" in r.text

            o = client.get("/api/overview?window=60").json()
            assert o["score"] == 100  # 低负载小样本应健康（R11 校准后 15s<20s）
            assert o["sample_count"] >= 5
            assert o["latest"]["kv_cache_usage_pct"] == 2.8

            s = client.get("/api/series?limit=10").json()
            assert len(s["t"]) == 5
            assert s["series"]["kv_cache_usage_pct"][-1] == 2.8

            h = client.get("/api/health").json()
            assert h["ok"] is True
            assert h["rows"] == 5
        finally:
            app.state.store.close()


def test_overview_on_empty_db_no_crash():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "empty.db")
        SQLiteStore(db).close()
        app = create_app(db)
        client = TestClient(app)
        try:
            o = client.get("/api/overview").json()
            assert o["score"] == 100
            assert o["sample_count"] == 0
        finally:
            app.state.store.close()
