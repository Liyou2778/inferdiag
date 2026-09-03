"""HTTP 抓取 /metrics 端点。"""

from __future__ import annotations

import time

import httpx

from .parse import normalize, parse_prometheus_text
from .models import Sample


def scrape_once(url: str, timeout: float = 10.0) -> str:
    """抓取一次并返回原始 Prometheus 文本。失败抛 httpx 异常。"""
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def scrape_sample(url: str, timeout: float = 10.0, engine: str = "auto") -> Sample:
    """抓取 + 解析 + 归一化为一个 Sample。"""
    text = scrape_once(url, timeout=timeout)
    snapshot = parse_prometheus_text(text)
    return normalize(snapshot, engine=engine)


def collect_loop(
    url: str,
    sink,  # 具备 insert_sample(sample) 的对象
    interval: float = 10.0,
    seconds: float = 0.0,
    timeout: float = 10.0,
    engine: str = "auto",
    verbose: bool = True,
) -> int:
    """按 interval 持续采集，累计 seconds 秒（<=0 表示无限）。返回采集次数。

    sink: 数据存储对象（见 store.SQLiteStore）。
    """
    start = time.time()
    n = 0
    while True:
        if seconds > 0 and time.time() - start >= seconds:
            break
        try:
            sample = scrape_sample(url, timeout=timeout, engine=engine)
            sink.insert_sample(sample)
            n += 1
            if verbose:
                print(f"[{n}] {sample.summary()}", flush=True)
        except Exception as exc:  # noqa: BLE001 采集失败不应中断整个循环
            print(f"[warn] scrape failed: {exc}", flush=True)
        time.sleep(interval)
    return n
