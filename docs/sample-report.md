# inferdiag 体检报告

- 健康分：**70/100**
- 样本数：30（窗口 100.0s）

## 诊断建议

- [warning] **R3 首 token 延迟(TTFT)偏高**（ttft_p50_ms=3916.67）
  - 实测 p50=40ms；p50>500ms 属明显劣化：检查超长请求队头阻塞、前缀缓存是否生效、是否需 PD 分离。
- [warning] **R7 吞吐偏低（算力可能浪费）**（generation_tokens_rate=41.406，num_running=4.0）
  - batch 没吃饱：调大 max_num_seqs / max_num_batched_tokens。
- [warning] **R11 长尾延迟显著恶化(p99)**（e2e_p99_ms=37666.67）
  - 实测长输出负载 p99≈15s；>20s 属显著恶化：长请求限流/超时、队列优先级、长请求独立池。

## 关键指标

- `e2e_p50_ms` = 30000.0
- `e2e_p99_ms` = 37666.67
- `generation_tokens_rate` = 41.406
- `idle_no_activity` = False
- `kv_cache_usage_pct` = 2.9
- `num_running` = 4.0
- `num_waiting` = 0.0
- `preemptions_rate` = 0.0
- `prefix_cache_hit_pct` = 100.0
- `prompt_tokens_rate` = 4.7884
- `requests_success_rate` = 0.0977
- `sample_count` = 30
- `tpot_ms` = 25.0
- `ttft_p50_ms` = 3916.67
- `ttft_p99_ms` = 10000.0
- `used_fallback_window` = True
- `window_seconds` = 71.6