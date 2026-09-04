"""FastAPI 仪表盘后端：从本地 SQLite 读样本并产出 JSON。

仅读本地库，不依赖推理引擎在线；所有接口相对路径，便于离线/内网部署。
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..cost import estimate_cost
from ..report import build_report
from ..store import SQLiteStore

STATIC_DIR = Path(__file__).parent / "static"

# 仪表盘展示的核心指标（与 /api/series 的 metric 名一一对应）
SERIES_METRICS = [
    "kv_cache_usage_pct",
    "ttft_p50_ms",
    "ttft_p99_ms",
    "e2e_p99_ms",
    "num_running",
    "num_waiting",
]


def create_app(
    db_path: str = "data/inferdiag.db",
    collect_url: str | None = None,
    collect_interval: float = 3.0,
    collect_engine: str = "auto",
) -> FastAPI:
    app = FastAPI(title="inferdiag", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.store = SQLiteStore(db_path)
    app.state.db_path = db_path
    app.state.collect_url = collect_url
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    if collect_url:
        import threading

        from ..collector.scrape import scrape_sample

        def _collector_loop() -> None:
            """后台采集线程：让仪表盘自带实时监控能力（边采边显示）。"""
            while True:
                try:
                    sample = scrape_sample(collect_url, engine=collect_engine)
                    app.state.store.insert_sample(sample)
                except Exception as exc:  # noqa: BLE001 引擎短暂不可用不应中断看板
                    print(f"[collector] scrape failed: {exc}", flush=True)
                time.sleep(max(0.5, collect_interval))

        threading.Thread(target=_collector_loop, name="inferdiag-collector", daemon=True).start()

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/overview")
    def overview(window: float = Query(120.0, ge=1)):
        """最新体检：健康分 + 触发规则 + 最近样本关键值。"""
        store: SQLiteStore = app.state.store
        metrics = store.window_metrics(window)
        cost = estimate_cost(metrics)
        report = build_report(metrics, window, cost)
        latest = store.latest(1)
        return {
            "score": report["score"],
            "sample_count": report["sample_count"],
            "window_seconds": window,
            "generated_at": time.time(),
            "rows": store.count(),
            "collecting": app.state.collect_url is not None,
            "findings": report["findings"],
            "metrics": {k: v for k, v in metrics.items() if v is not None},
            "cost": report["cost"],
            "latest": latest[0].to_dict() if latest else None,
        }

    @app.get("/api/series")
    def series(
        limit: int = Query(60, ge=2, le=500),
        metrics: str = Query(",".join(SERIES_METRICS)),
    ):
        """最近 limit 条样本的时序（用于画曲线）。metric 逗号分隔。"""
        store: SQLiteStore = app.state.store
        samples = list(reversed(store.latest(limit)))
        keys = [m.strip() for m in metrics.split(",") if m.strip()]
        out = {"t": [round(s.ts, 1) for s in samples], "series": {}}
        for key in keys:
            out["series"][key] = [getattr(s, key, None) for s in samples]
        return out

    @app.get("/api/health")
    def health():
        store: SQLiteStore = app.state.store
        return {
            "ok": True,
            "db": str(store.db_path),
            "rows": store.count(),
            "collect_url": app.state.collect_url,
            "collecting": app.state.collect_url is not None,
        }

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    return app
