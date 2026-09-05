"""FastAPI 仪表盘后端：从本地 SQLite 读样本并产出 JSON。

仅读本地库，不依赖推理引擎在线；所有接口相对路径，便于离线/内网部署。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..cost import PRICING, estimate_cost
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
    app.state.engine_base = None
    if collect_url and "/metrics" in collect_url:
        app.state.engine_base = collect_url.split("/metrics", 1)[0]
    # 一键演示压测控制器
    app.state.demo = {"stop": True, "active": False, "ok": 0, "err": 0, "model": None}
    # 一键检测状态机
    app.state.scan = {
        "running": False, "done": False, "stop": False, "started_at": 0,
        "duration": 0, "step": 0, "log": [], "report": None, "error": None,
    }
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
                time.sleep(max(0.2, collect_interval))

        threading.Thread(target=_collector_loop, name="inferdiag-collector", daemon=True).start()

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        # 用 app.js 的 mtime 做 cache-bust，前端更新后无需手动改版本号
        js = STATIC_DIR / "app.js"
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("app.js?v=AUTO", f"app.js?v={int(js.stat().st_mtime)}")
        return HTMLResponse(html)

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
            "pricing": (
                {"input_per_mtok": PRICING["input_per_mtok"], "output_per_mtok": PRICING["output_per_mtok"]}
                if PRICING else None
            ),
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

    # ---------- 一键演示压测（让曲线"跳起来"） ----------

    def _discover_model(base: str) -> str | None:
        """从引擎 OpenAI 兼容 /v1/models 发现服务模型名。"""
        import urllib.request

        try:
            with urllib.request.urlopen(f"{base}/v1/models", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["data"][0]["id"]
        except Exception:  # noqa: BLE001
            return None

    def _demo_worker(params: dict) -> None:
        import threading
        import urllib.request

        base = app.state.engine_base
        model = app.state.demo["model"] or _discover_model(base)
        if not model:
            return
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "请写一篇短文介绍人工智能的发展前景，约500字。"}],
            "max_tokens": int(params.get("max_tokens", 400)),
        }
        workers = int(params.get("workers", 3))
        total = int(params.get("requests", 12))
        lock = threading.Lock()
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        def _fire() -> None:
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    resp.read()
                with lock:
                    app.state.demo["ok"] += 1
            except Exception:  # noqa: BLE001
                with lock:
                    app.state.demo["err"] += 1

        sent = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while sent < total and not app.state.demo["stop"]:
                for _ in range(min(workers, total - sent)):
                    pool.submit(_fire)
                    sent += 1
                time.sleep(0.5)
        app.state.demo["active"] = False

    @app.post("/api/demo/start")
    async def demo_start(request: Request):
        if app.state.collect_url is None or not app.state.engine_base:
            return JSONResponse({"ok": False, "error": "当前非实时采集模式（serve 未加 -m），无法演示压测"})
        if app.state.demo["active"]:
            return JSONResponse({"ok": False, "error": "演示压测已在进行中"})
        params = await request.json()
        app.state.demo.update({"stop": False, "active": True, "ok": 0, "err": 0})
        app.state.demo["model"] = _discover_model(app.state.engine_base)
        import threading

        threading.Thread(target=_demo_worker, args=(params,), daemon=True).start()
        return JSONResponse({"ok": True, "active": True, "model": app.state.demo["model"]})

    @app.post("/api/demo/stop")
    async def demo_stop():
        app.state.demo["stop"] = True
        return JSONResponse({"ok": True, "stopping": True})

    @app.get("/api/demo/status")
    def demo_status():
        d = app.state.demo
        return {
            "active": d["active"],
            "ok": d["ok"],
            "err": d["err"],
            "engine_base": app.state.engine_base,
            "model": d["model"],
        }

    # ---------- 一键检测：点一下 -> 实时采集 -> 自动出报告 ----------

    def _scan_worker(duration: int) -> None:
        from ..collector.scrape import scrape_sample

        store = app.state.store
        sc = app.state.scan
        sc["log"] = []
        sc["error"] = None
        sc["started_at"] = time.time()
        for i in range(1, duration + 1):
            if sc["stop"]:
                break
            try:
                sample = scrape_sample(collect_url, engine=collect_engine)
                store.insert_sample(sample)
                msg = (
                    f"[{i}/{duration}] 采集完成 running={sample.num_running} "
                    f"kv={sample.kv_cache_usage_pct}% ttft_p50={sample.ttft_p50_ms}ms"
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"[{i}/{duration}] 采集失败: {exc}"
            sc["step"] = i
            sc["log"].append(msg)
            if len(sc["log"]) > 60:
                sc["log"] = sc["log"][-60:]
            time.sleep(1)

        metrics = store.window_metrics(duration + 10)
        report = build_report(metrics, duration + 10, estimate_cost(metrics))
        sc["report"] = {
            "score": report["score"],
            "sample_count": report["sample_count"],
            "window_seconds": report["window_seconds"],
            "findings": report["findings"],
            "metrics": {k: v for k, v in metrics.items() if v is not None},
            "generated_at": time.time(),
        }
        sc["log"].append(f"检测完成：健康分 {report['score']}/100，样本 {report['sample_count']} 条")
        sc["running"] = False
        sc["done"] = True

    @app.post("/api/scan/start")
    async def scan_start(request: Request):
        if not app.state.collect_url or not app.state.engine_base:
            return JSONResponse({"ok": False, "error": "非实时采集模式（serve 未加 -m），无法一键检测"})
        sc = app.state.scan
        if sc["running"]:
            return JSONResponse({"ok": False, "error": "检测已在进行中"})
        body = await request.json()
        duration = int(max(5, min(180, body.get("duration", 30))))
        sc.update({"running": True, "done": False, "step": 0, "duration": duration, "stop": False})
        import threading

        threading.Thread(target=_scan_worker, args=(duration,), daemon=True).start()
        return JSONResponse({"ok": True, "duration": duration})

    @app.post("/api/scan/stop")
    async def scan_stop():
        app.state.scan["stop"] = True
        return JSONResponse({"ok": True})

    @app.get("/api/scan/status")
    def scan_status():
        sc = app.state.scan
        elapsed = round(time.time() - sc["started_at"], 1) if sc["started_at"] else 0
        return {
            "running": sc["running"],
            "done": sc["done"],
            "duration": sc["duration"],
            "step": sc["step"],
            "elapsed": elapsed,
            "log": sc["log"],
            "report": sc["report"],
            "error": sc["error"],
            "engine_base": app.state.engine_base,
        }

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    return app
