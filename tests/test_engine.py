"""规则引擎测试。"""

from inferdiag.report import build_report, health_score
from inferdiag.rules.engine import evaluate, load_rules


def _stress_metrics():
    return {
        "sample_count": 5,
        "num_running": 32.0,
        "num_waiting": 40.0,
        "kv_cache_usage_pct": 97.0,
        "ttft_p50_ms": 1800.0,
        "tpot_ms": 90.0,
        "e2e_p99_ms": 12000.0,
        "preemptions_rate": 5.0,
        "requests_success_rate": 30.0,
        "generation_tokens_rate": 700000.0,
        "prefix_cache_hit_pct": None,  # 缺失 → 相关规则应跳过
    }


def test_rules_trigger_on_stress():
    rules = load_rules()
    findings = evaluate(_stress_metrics(), rules)
    ids = {f["rule_id"] for f in findings}
    # stress 场景应触发核心规则
    assert {"R1", "R2", "R3", "R9", "R12", "R14"} <= ids
    # 指标缺失的 R6 不应被误触发
    assert "R6" not in ids


def test_missing_metric_skips_rule():
    metrics = {"num_running": 5.0}  # 孤立指标：所有需要它作条件的规则因缺其它指标而跳过
    findings = evaluate(metrics)
    assert findings == []


def test_healthy_normal_no_critical():
    metrics = {
        "sample_count": 5,
        "num_running": 5.0,
        "num_waiting": 2.0,
        "kv_cache_usage_pct": 40.0,
        "ttft_p50_ms": 350.0,
        "tpot_ms": 40.0,
        "e2e_p99_ms": 2000.0,
        "preemptions_rate": 0.1,
        "requests_success_rate": 5.0,
        "generation_tokens_rate": 120000.0,
    }
    findings = evaluate(metrics)
    assert all(f["level"] != "critical" for f in findings)


def test_health_score_penalty():
    assert health_score([]) == 100
    assert health_score([{"level": "critical"}, {"level": "warning"}, {"level": "info"}]) == 62


def test_build_report_structure():
    r = build_report(_stress_metrics(), window_seconds=60.0)
    assert "score" in r and "findings" in r and "notes" in r
    assert r["findings"][0]["level"] == "critical"  # 排序后 critical 在前
