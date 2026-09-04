"""持续轻负载：周期性发小批量请求，让推理曲线持续"跳动"（演示/压测用）。

用法:
    python scripts/continuous_load.py ^
        --url http://localhost:8000/v1/chat/completions ^
        --model /home/liyou/qwen3b-awq ^
        --workers 3 --batch-every 4 --max-tokens 250
Ctrl+C 停止。
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
_start = time.time()


def _fire(url: str, payload: dict) -> None:
    global _OK, _ERR
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            json.loads(resp.read().decode())
        with _lock:
            _OK += 1
    except Exception:  # noqa: BLE001
        with _lock:
            _ERR += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--batch-every", type=float, default=4.0, help="每隔几秒发一批")
    ap.add_argument("--max-tokens", type=int, default=250)
    args = ap.parse_args()

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "请写一段300字左右的短文介绍机器学习的基本概念。"}],
        "max_tokens": args.max_tokens,
    }
    print(f"continuous load: workers={args.workers} every {args.batch_every}s, Ctrl+C 停止")
    try:
        while True:
            active = []
            for _ in range(args.workers):
                t = threading.Thread(target=_fire, args=(args.url, payload), daemon=True)
                t.start()
                active.append(t)
            time.sleep(args.batch_every)
            with _lock:
                print(f"t+{time.time()-_start:.0f}s ok={_OK} err={_ERR}", flush=True)
    except KeyboardInterrupt:
        print(f"stopped. ok={_OK} err={_ERR}")


if __name__ == "__main__":
    main()
