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
