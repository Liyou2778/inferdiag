# 阈值校准记录（v0.1）

## 校准环境
- 硬件：RTX 4060 Laptop 8GB（WSL2，pin_memory 关闭略有损耗）
- 引擎：vLLM 0.8.5（V1 engine），Qwen/Qwen2.5-3B-Instruct-AWQ
- 配置：`--max-model-len 4096 --gpu-memory-utilization 0.8 --swap-space 4`
- 引擎容量日志：GPU KV cache 55,552 tokens，4096ctx 下最大并发 ≈13.5

## 实测基线（2026-09-04）

| 指标 | 空闲 | 正常负载（4 并发 ×14–16 条 ~950 token 请求） |
|---|---|---|
| num_running | 0 | 峰值 4 |
| num_waiting | 0 | 0（抽样粒度未捕捉到瞬时队列） |
| kv_cache_usage_pct | 0.0 | 峰值 2.8–3.1% |
| ttft_p50 / p99 | — | 40ms / 2.5s（并发队尾等待） |
| tpot | — | 25ms |
| e2e_p50 / p99 | — | 10–11s / 15s（长输出生成主导） |
| 到达率 requests_success_rate | 0 | ≈0.18–0.38 req/s |
| 前缀缓存命中 | — | 100%（压测用同一 prompt） |
| 抢占 | 0 | 0 |

## 校准规则（阈值 ≈ 正常负载峰值 × 1.5~2）

| 规则 | v0 初始 | v0.1 校准 | 依据 |
|---|---|---|---|
| R2 队列积压 num_waiting | ≥30 | ≥8 | 本机最大并发≈13，排队≥8≈饱和 |
| R3 TTFT p50 | ≥1000ms | ≥500ms | 实测 p50=40ms |
| R5 KV 容量规划 | ≥80% | ≥50% | 实测负载 KV≈3% |
| R10 低峰空转 | kv≤15% | kv≤2% | 空闲 0%，负载 3% |
| R11 e2e p99 | ≥5000ms | ≥20000ms | 实测长输出负载 p99≈15s |
| R12 PD 分离信号 | ttft≥1000 | ttft≥500 且等待≥8 | 对齐 R3/R2 |
| R14 扩容 | waiting≥20, rate≥20 | waiting≥8, rate≥0.3 | 实测到达率 0.2–0.4 req/s |

## 已知局限（v0.1）
1. **R10 尾巴窗口误报**：负载结束后的一段时间（引擎空闲但窗口含负载尾巴）会触发 R10 info——
   因指标为聚合窗口、无时间子窗；计划 v0.2 用"子窗口分段"消除。
2. **分位数是 histogram 上界近似**：vLLM bucket 较粗（e2e 为 10s/15s 档），p50/p99 为所在 bucket 上界。
3. 换硬件/模型/上下文长度后需重新跑一遍"空闲基线 + 正常负载基线"（scripts/pressure_test.py + inferdiag collect 完成）。
