"""对 OpenAI 兼容推理端点做并发压力测试（inferdiag 配套）。

用法（Windows 或任意 Python 3 环境，纯标准库）:
    python scripts/pressure_test.py ^
        --url http://localhost:8000/v1/chat/completions ^
        --model /home/liyou/qwen3b-awq ^
        --workers 4 --requests 12 --max-tokens 600

建议：跑起来的同时，另开终端执行
    uv run inferdiag collect --url http://localhost:8000/metrics --interval 2 --seconds 60
让采样窗口覆盖压测过程，KV cache / TTFT 指标才会显形。
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request

_OK = 0
_ERR = 0
_lock = threading.Lock()
_t0 = time.time()


def _hit(url: str, payload: dict) -> None:
    global _OK, _ERR
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            # 取一段输出确认真的生成了内容
            text = body["choices"][0]["message"]["content"]
            with _lock:
                _OK += 1
                print(f"[ok] {_OK:3d}  tokens={len(text)}  ({time.time()-_t0:.0f}s)", flush=True)
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _ERR += 1
            print(f"[err] {_ERR:3d}  {type(exc).__name__}: {exc}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--workers", type=int, default=4, help="并发请求数")
    ap.add_argument("--requests", type=int, default=12, help="总请求数")
    ap.add_argument("--max-tokens", type=int, default=600)
    args = ap.parse_args()

    payload = {
        "model": args.model,
        "messages": [
            {"role": "user", "content": "请写一篇约800字的文章，介绍人工智能在医疗和交通领域的应用前景。"}
        ],
        "max_tokens": args.max_tokens,
    }

    print(f"pressure: workers={args.workers} requests={args.requests} -> {args.url}")
    threads = []
    for _ in range(args.requests):
        while sum(1 for t in threads if t.is_alive()) >= args.workers:
            time.sleep(0.1)
        t = threading.Thread(target=_hit, args=(args.url, payload), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    print(f"done: ok={_OK} err={_ERR} elapsed={time.time()-_t0:.0f}s")


if __name__ == "__main__":
    main()
