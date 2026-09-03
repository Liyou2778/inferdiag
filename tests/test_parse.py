"""解析器与归一化测试。"""

from pathlib import Path

from inferdiag.collector.parse import normalize, parse_prometheus_text

FIXTURE = Path(__file__).parent / "fixtures" / "vllm_sample.txt"


def test_parse_fixture_contains_families():
    text = FIXTURE.read_text(encoding="utf-8")
    snap = parse_prometheus_text(text)
    assert "vllm:gpu_cache_usage_perc" in snap
    assert "vllm:time_to_first_token_seconds" in snap


def test_normalize_fills_core_fields():
    text = FIXTURE.read_text(encoding="utf-8")
    snap = parse_prometheus_text(text)
    s = normalize(snap)
    assert s.engine == "vllm"
    assert s.kv_cache_usage_pct == 64.0
    assert s.num_running == 8
    assert s.num_waiting == 3
    assert s.preemptions_total == 17
    assert s.ttft_p50_ms == 350.0
    assert s.ttft_p99_ms == 1900.0


def test_normalize_empty_snapshot_tolerated():
    s = normalize({})
    assert s.kv_cache_usage_pct is None
    assert s.num_running is None


def test_histogram_quantile_estimation():
    """vLLM 0.8.5 的延迟指标是 histogram：应从 _bucket 估算出 p50/p99。"""
    text = """# HELP vllm:time_to_first_token_seconds time to first token
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{le="0.05"} 0
vllm:time_to_first_token_seconds_bucket{le="0.1"} 0
vllm:time_to_first_token_seconds_bucket{le="0.25"} 2
vllm:time_to_first_token_seconds_bucket{le="0.5"} 5
vllm:time_to_first_token_seconds_bucket{le="1.0"} 7
vllm:time_to_first_token_seconds_bucket{le="2.5"} 9
vllm:time_to_first_token_seconds_bucket{le="5.0"} 10
vllm:time_to_first_token_seconds_bucket{le="+Inf"} 10
vllm:time_to_first_token_seconds_sum 3.2
vllm:time_to_first_token_seconds_count 10
# HELP vllm:num_preemptions_total preemptions
# TYPE vllm:num_preemptions_total counter
vllm:num_preemptions_total 3
"""
    snap = parse_prometheus_text(text)
    s = normalize(snap)
    # p50: 第5个(累计≥5)→ bucket le=0.5 → 500ms；p99: 累计≥9.9 → bucket le=5.0 → 5000ms
    assert s.ttft_p50_ms == 500.0
    assert s.ttft_p99_ms == 5000.0
    assert s.preemptions_total == 3
