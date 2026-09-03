"""Mock /metrics 服务：无 GPU / 无真实引擎时开发用。

模拟 vLLM 风格指标：量规型数值随时间波动；计数型(counter)单调递增，
stress 模式制造"高负载 + KV 打满 + 排队 + 抢占"，用于触发诊断规则 R1/R2/R3/R9/R12/R14。

用法:
    python scripts/serve_mock_metrics.py --port 8001            # normal
    python scripts/serve_mock_metrics.py --port 8001 --mode stress
"""

from __future__ import annotations

import argparse
import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 计数型指标的状态：按时间累积，保证单调递增（规则引擎按差值算速率）
_STATE = {"t": None, "prompt": 0.0, "gen": 0.0, "success": 0.0, "preempt": 0.0}


def _advance_counters(mode: str, load: float) -> None:
    now = time.time()
    dt = 0.0 if _STATE["t"] is None else now - _STATE["t"]
    _STATE["t"] = now
    if dt <= 0 or dt > 30:  # 首次调用或异常间隔
        return
    stress = mode == "stress"
    _STATE["prompt"] += (150_000 if stress else 20_000 + 80_000 * load) * dt
    _STATE["gen"] += (700_000 if stress else 60_000 + 260_000 * load) * dt
    _STATE["success"] += (30 if stress else 1 + 8 * load) * dt
    _STATE["preempt"] += (5 if stress else 0.05 + 0.3 * load) * dt


def _fmt(name: str, value: float, typ: str = "gauge", help_text: str = "") -> str:
    return f"# HELP {name} {help_text}\n# TYPE {name} {typ}\n{name} {value:.6f}"


def build_metrics(mode: str = "normal") -> str:
    t = time.time()
    hour = time.localtime(t).tm_hour
    day_factor = 1.0 if 8 <= hour <= 22 else 0.15
    wave = 0.5 + 0.5 * math.sin(t / 30.0)
    load = max(0.0, day_factor * (0.5 + 0.5 * wave))

    _advance_counters(mode, load)

    if mode == "stress":
        running, waiting, kv = 32, 40, 0.97
        preempt_show, ttft_p50, ttft_p99 = _STATE["preempt"], 1.8, 6.5
        tpot, e2e_p50, e2e_p99 = 0.09, 3.2, 12.0
    else:
        running = round(2 + 20 * load)
        waiting = round(1 + 6 * load)
        kv = 0.25 + 0.5 * load
        preempt_show = _STATE["preempt"]
        ttft_p50 = 0.18 + load * 0.35
        ttft_p99 = 0.6 + load * 1.6
        tpot = 0.04
        e2e_p50 = 0.3 + load * 0.8
        e2e_p99 = 1.0 + load * 3.0

    parts = [
        _fmt("vllm:num_requests_running", float(running), "gauge", "running requests"),
        _fmt("vllm:num_requests_waiting", float(waiting), "gauge", "waiting requests"),
        _fmt("vllm:num_requests_swapped", load * 3, "gauge"),
        _fmt("vllm:num_preemptions_total", preempt_show, "counter"),
        _fmt("vllm:request_success_total", _STATE["success"], "counter"),
        _fmt("vllm:gpu_cache_usage_perc", kv, "gauge", "fraction of KV cache used"),
        _fmt("vllm:cpu_cache_usage_perc", kv * 0.1, "gauge"),
        _fmt("vllm:prompt_tokens_total", _STATE["prompt"], "counter"),
        _fmt("vllm:generation_tokens_total", _STATE["gen"], "counter"),
        "# HELP vllm:time_to_first_token_seconds time to first token",
        "# TYPE vllm:time_to_first_token_seconds summary",
        f'vllm:time_to_first_token_seconds{{quantile="0.5"}} {ttft_p50:.4f}',
        f'vllm:time_to_first_token_seconds{{quantile="0.99"}} {ttft_p99:.4f}',
        "vllm:time_to_first_token_seconds_count 1",
        "vllm:time_to_first_token_seconds_sum 1",
        "# HELP vllm:time_per_output_token_seconds time per output token",
        "# TYPE vllm:time_per_output_token_seconds summary",
        f"vllm:time_per_output_token_seconds {tpot:.4f}",
        "vllm:time_per_output_token_seconds_count 1",
        "vllm:time_per_output_token_seconds_sum 1",
        "# HELP vllm:e2e_request_latency_seconds end-to-end latency",
        "# TYPE vllm:e2e_request_latency_seconds summary",
        f'vllm:e2e_request_latency_seconds{{quantile="0.5"}} {e2e_p50:.4f}',
        f'vllm:e2e_request_latency_seconds{{quantile="0.99"}} {e2e_p99:.4f}',
        "vllm:e2e_request_latency_seconds_count 1",
        "vllm:e2e_request_latency_seconds_sum 1",
    ]
    return "\n".join(parts) + "\n"


class Handler(BaseHTTPRequestHandler):
    mode = "normal"

    def do_GET(self):  # noqa: N802
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        body = build_metrics(self.mode).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默访问日志
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock /metrics for inferdiag dev")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--mode", choices=["normal", "stress"], default="normal")
    args = parser.parse_args()
    Handler.mode = args.mode
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock /metrics listening on http://127.0.0.1:{args.port}/metrics (mode={args.mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
