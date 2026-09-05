"""inferdiag CLI。

已实现：collect / check / report / export / serve。
"""

from __future__ import annotations

import sys

import typer

from .collector.scrape import collect_loop
from .cost import estimate_cost
from .report import build_report
from .store import SQLiteStore

try:  # Windows 控制台中文输出
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001, S110 编码不可用时保持默认
    pass

app = typer.Typer(add_completion=False, help="LLM 推理体检报告生成器")

_LEVEL_ICON = {"critical": "[CRIT]", "warning": "[WARN]", "info": "[INFO]"}


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


def _render_findings(findings: list[dict]) -> str:
    if not findings:
        return "未发现明显问题 ✓（若怀疑有问题，请先采集更长时间/校准阈值）"
    lines = []
    for f in findings:
        icon = _LEVEL_ICON.get(f["level"], "[?]")
        ev = "，".join(f"{k}={v}" for k, v in f["evidence"].items())
        lines.append(f"{icon} {f['rule_id']} {f['name']}  (证据: {ev})")
        lines.append(f"     建议: {f['suggestion']}")
    return "\n".join(lines)


@app.command()
def check(
    db: str = typer.Option("data/inferdiag.db", "--db"),
    window: float = typer.Option(120.0, "--window", help="统计时间窗(秒)"),
) -> None:
    """对最近数据运行诊断规则，输出体检报告。"""
    store = SQLiteStore(db)
    try:
        metrics = store.window_metrics(window)
        cost_info = estimate_cost(metrics)
        report = build_report(metrics, window, cost_info)
        print("=" * 60)
        print(f"inferdiag 体检报告  |  健康分: {report['score']}/100")
        print(f"窗口: {window}s  样本数: {report['sample_count']}")
        print("-" * 60)
        print(_render_findings(report["findings"]))
        if report["cost"]:
            c = report["cost"]
            print(f"- 窗口成本估算: {c['window_cost_rmb']} 元 (输入 {c['window_tokens_in']} tok / 输出 {c['window_tokens_out']} tok)")
        print("=" * 60)
    finally:
        store.close()


@app.command()
def report(
    db: str = typer.Option("data/inferdiag.db", "--db"),
    window: float = typer.Option(120.0, "--window", help="统计时间窗(秒)"),
) -> None:
    """详细版体检报告（含指标快照与注意事项）。"""
    store = SQLiteStore(db)
    try:
        metrics = store.window_metrics(window)
        cost_info = estimate_cost(metrics)
        r = build_report(metrics, window, cost_info)
        print("=" * 60)
        print("inferdiag 体检报告（详细版）")
        print("=" * 60)
        print(f"健康分: {r['score']}/100  (100-25×CRIT-10×WARN-3×INFO)")
        print(f"样本数: {r['sample_count']}   时间窗: {window}s")
        print("-" * 60)
        print("触发规则:")
        print(_render_findings(r["findings"]))
        print("-" * 60)
        if r["metrics_snapshot"]:
            print("关键指标(窗口聚合):")
            for k, v in sorted(r["metrics_snapshot"].items()):
                if v is not None:
                    print(f"  {k} = {v}")
        if r["cost"]:
            c = r["cost"]
            print(f"成本估算: {c['window_cost_rmb']} 元 (输入 {c['window_tokens_in']} / 输出 {c['window_tokens_out']})")
        else:
            print("成本: 未配置单价表（见 inferdiag/cost.py PRICING）")
        print("-" * 60)
        for n in r["notes"]:
            print(f"注意: {n}")
        print("=" * 60)
    finally:
        store.close()


@app.command()
def serve(
    db: str = typer.Option("data/inferdiag.db", "--db"),
    host: str = "127.0.0.1",
    port: int = 8080,
    collect_url: str = typer.Option(
        None, "--collect-url", "-m", help="推理引擎 /metrics 地址；提供后仪表盘自动实时采集"
    ),
    collect_interval: float = typer.Option(3.0, "--collect-interval", help="实时采集间隔(秒)"),
) -> None:
    """启动 Web 仪表盘。传 -m 引擎 /metrics 地址即可边采边看（实时监控）。"""
    import uvicorn

    from .web.app import create_app

    web_app = create_app(db, collect_url=collect_url, collect_interval=collect_interval)
    tag = f"，实时采集 {collect_url}" if collect_url else "（静态库模式）"
    print(f"inferdiag dashboard -> http://{host}:{port}  (db={db}{tag})")
    uvicorn.run(web_app, host=host, port=port, log_level="info")


@app.command()
def export(
    db: str = typer.Option("data/inferdiag.db", "--db"),
    window: float = typer.Option(120.0, "--window", help="统计时间窗(秒)"),
    fmt: str = typer.Option("json", "--format", "-f", help="json | md"),
    out: str = typer.Option("report", "--out", "-o", help="输出路径（自动补扩展名）"),
) -> None:
    """把体检报告导出为文件（JSON / Markdown）。"""
    import json
    from pathlib import Path

    from .cost import estimate_cost
    from .report import build_report

    store = SQLiteStore(db)
    try:
        metrics = store.window_metrics(window)
        report = build_report(metrics, window, estimate_cost(metrics))
        path = Path(out)
        if path.suffix not in (".json", ".md"):
            path = path.with_suffix("." + fmt)

        if fmt == "md":
            lines = [
                "# inferdiag 体检报告",
                "",
                f"- 健康分：**{report['score']}/100**",
                f"- 样本数：{report['sample_count']}（窗口 {window}s）",
                "",
                "## 诊断建议",
                "",
            ]
            if report["findings"]:
                for f in report["findings"]:
                    ev = "，".join(f"{k}={v}" for k, v in f["evidence"].items())
                    lines.append(f"- [{f['level']}] **{f['rule_id']} {f['name']}**（{ev}）")
                    lines.append(f"  - {f['suggestion']}")
            else:
                lines.append("未发现明显问题 ✓")
            lines += ["", "## 关键指标", ""]
            for k, v in sorted(metrics.items()):
                if v is not None:
                    lines.append(f"- `{k}` = {v}")
            content = "\n".join(lines)
        else:
            content = json.dumps(report, ensure_ascii=False, indent=2)

        path.write_text(content, encoding="utf-8")
        print(f"已导出: {path}")
    finally:
        store.close()


if __name__ == "__main__":
    app()
