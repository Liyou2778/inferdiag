"""inferdiag CLI。

v0.1 阶段已实现：collect（采集入库）。
P3/P4 待实现命令（check/report/serve）先给占位提示。
"""

from __future__ import annotations

import typer

from .collector.scrape import collect_loop
from .store import SQLiteStore

app = typer.Typer(add_completion=False, help="LLM 推理体检报告生成器")


@app.command()
def collect(
    url: str = typer.Option("http://127.0.0.1:8001/metrics", "--url", "-u", help="/metrics 地址"),
    db: str = typer.Option("data/inferdiag.db", "--db", help="SQLite 路径"),
    interval: float = typer.Option(10.0, "--interval", help="采集间隔(秒)"),
    seconds: float = typer.Option(30.0, "--seconds", help="累计采集时长(秒)，<=0 为无限"),
    engine: str = typer.Option("auto", "--engine", help="auto|vllm|sglang"),
) -> None:
    """定时抓取 /metrics 并写入 SQLite。"""
    store = SQLiteStore(db)
    try:
        print(f"collecting from {url} -> {db} (interval={interval}s, seconds={seconds})")
        n = collect_loop(url, store, interval=interval, seconds=seconds, engine=engine)
        print(f"done: {n} samples stored, total rows={store.count()}")
    finally:
        store.close()


@app.command()
def check(
    db: str = typer.Option("data/inferdiag.db", "--db"),
) -> None:
    """对最近样本运行诊断规则（P3 实现）。"""
    typer.echo("check: 规则引擎将在下一阶段(P3)实现，当前版本先看采集结果。")
    store = SQLiteStore(db)
    try:
        rows = store.count()
        typer.echo(f"当前库中共有 {rows} 条样本。先完成 P2 采集，再来做诊断。")
    finally:
        store.close()


@app.command()
def report(
    db: str = typer.Option("data/inferdiag.db", "--db"),
) -> None:
    """生成体检报告（P3 实现）。"""
    typer.echo("report: 将在 P3 与规则引擎一起实现。")


@app.command()
def serve(
    db: str = typer.Option("data/inferdiag.db", "--db"),
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """启动 Web 仪表盘（P4 实现）。"""
    typer.echo(f"serve: Web 仪表盘将在 P4 实现（届时访问 http://{host}:{port}）。")


if __name__ == "__main__":
    app()
