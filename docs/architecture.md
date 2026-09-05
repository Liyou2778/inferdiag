# inferdiag 架构文档

> v0.1 草案 ｜ 对应 README 中的"架构速览"详细版

---

## 1. 项目定义（为什么是它）

**一句话**：inferdiag 是 vLLM/SGLang 的"体检报告生成器"——输入引擎指标，输出"为什么慢、为什么贵、该怎么改、改了有没有用"。

**选它的逻辑链**（综合前序调研）：
1. 求职目标 = AI Infra 系统工程师 → 产品必须能向下挖到引擎机制（KV cache、批处理、调度、PD 分离），这是简历与面试弹药；
2. 需要有真实用户价值 → 用户是"自己部署开源模型的中小团队/个人"，痛点是真实的；
3. 差异化定位 → GitHub 现状：监控面板（红海）、KV/显存显示（已被 vllmstat 等覆盖）、成本记账（已有 vllm-cost-meter）→ **"指标 → 诊断 → 建议 → 实验验证"的闭环层几乎空白**；
4. 可一人开发 → v0 全部用现成库，SQLite 起步，不需要 GPU（Mock 源可离线开发）；
5. 可变现/引流 → 开源免费引流 + 后续托管版/私有化；可参赛 → 国产卡适配(v0.3)对接昇腾系开发者大赛；
6. 长期可打磨 → 规则库、存储、MCP、K8s 版本逐层演进。

**Personas**
- P1 个人开发者/极客：一台 4090 部署 7B，想知道"显存够不够、该不该量化、月度成本多少"。
- P2 中小团队（1–3 人运维）：生产 vLLM 服务，白天慢晚上 OOM，需要"5 分钟定位 + 可执行建议"。
- P3 私有化交付服务商：给客户部署模型，把"健康看板 + 优化报告"当交付物的一部分（本产品的付费场景）。
- P4（本人）AI Infra 求职者：README、架构文档、规则表本身 = 作品集；PR/开源记录 = 内推券。

## 2. Goals / Non-Goals

**Goals**
- 引擎级指标 → 结构化诊断报告（JSON），多端渲染（CLI/Web/未来的 LLM 总结）；
- 规则可配置（YAML），不写死；
- 采集器只依赖"引擎自带 /metrics"，不要求安装 Prometheus；
- 无 GPU / 无引擎也能开发与演示（Mock 源 + fixtures 回放）；
- 成本可见：每 token 成本与"浪费金额"。

**Non-Goals（v0 明确不做）**
- 不替代 Grafana/Prometheus 的展示与告警体系（可选集成，不内置重造）；
- 不做 API 网关/流量代理/缓存；
- 不做训练阶段监控；
- 不做端上数据采集与商业遥测（默认全部本地）。
- 不内置正式压测工具：正式压测走外部脚本（scripts/pressure_test.py）；仪表盘内置压测仅用于演示。

## 3. 系统上下文

```
                       ┌─────────────────────────────┐
                       │  inferdiag (本产品)           │
┌─────────────┐  HTTP  │ ┌─────────────────────────┐ │
│ vLLM/SGLang │───────►│ │ Collector               │ │
│ :8000/metrics│       │ │ scrape → parse → normalize│ │
└─────────────┘        │ └───────────┬─────────────┘ │
                       │             ▼               │
┌─────────────┐        │ ┌─────────────────────────┐ │
│ Mock 源      │───────►│ │ Store (SQLite v0)        │ │
│ :8001/metrics│        │ └───────────┬─────────────┘ │
└─────────────┘        │             ▼               │
                       │ ┌─────────────────────────┐ │
                       │ │ Rules Engine (R1–R14)   │ │
                       │ │ thresholds ← YAML       │ │
                       │ └───────────┬─────────────┘ │
                       │             ▼               │
                       │ ┌─────────────────────────┐ │
                       │ │ Report (JSON 结构化)     │ │
                       │ │ + Cost 成本归因          │ │
                       │ └───────┬────────┬────────┘ │
                       └─────────┼────────┼─────────┘
                                 ▼        ▼
                         CLI/Terminal   Web UI (FastAPI + 单页)
                                │
                                └── (v0.2) OpenAI 兼容 LLM → 人话总结
```

## 4. 模块职责

| 模块 | 职责 | 关键点 |
|---|---|---|
| `collector.scrape` | 定时(默认 10s) HTTP GET `/metrics` | 支持 auth header；超时与重试 |
| `collector.parse` | Prometheus 文本 → 内部 28 项字段模型 | 用 `prometheus_client` 的文本解析器；**字段名兼容层**：vLLM/SGLang 命名差异在此归一（如 `vllm:gpu_cache_usage_perc` vs SGLang 兼容名） |
| `collector.models` | 28 项字段 + 标签(model/instance/version) | 见《采集清单》文档 |
| `store` | 时序落库 | v0: SQLite(samples 表)；预留 `Storage` 接口，后续 Timescale/ClickHouse |
| `rules.engine` | 对最近时间窗样本跑规则集 | 输入指标 → 输出 CheckupItem{rule_id, level, evidence, suggestion} |
| `rules.rules_v0.yaml` | R1–R14 阈值与文案 | 纯配置，用户可改；含"基线建议"注释 |
| `cost` | token×单价 → 每 token 成本/浪费金额 | 单价表在 `config.py`(模型/输入/输出/卡型/时段) |
| `report` | 汇总 → 健康分 + 瓶颈排序 + 建议清单 | 输出 JSON；CLI/Web/LLM 三种渲染共用 |
| `web` | FastAPI + 静态单页 | REST: /api/checkups, /api/metrics/latest, /api/report |
| `cli` | `collect/check/report/serve` | Typer |
| `mock` + `scripts` | 假指标生成器 | 指标随负载/时间波动，便于演示告警与规则触发 |

## 5. 数据流（一次完整"体检"）

1. `inferdiag collect --url <engine>`：采集 N 个时间点（或持续后台采集）；
2. 每个时间点：parse → normalize(28 字段) → 写 `samples`；
3. `inferdiag check --model qwen3b`：取最近时间窗 → rules engine 依 YAML 阈值逐条评估；
4. engine 输出 CheckupItems → report 汇总（健康分 = 100 − Σ(权重×违规数)）；
5. cost 模块叠加单价 → 报告含"本月预估成本 / 可节省金额(R10/R7)"；
6. CLI 打印 / Web 展示 / （v0.2）LLM 把报告翻成人话。

## 6. 数据模型（v0，SQLite）

```
samples(id, ts, model, instance,
        num_running, num_waiting, num_swapped,
        preemptions_rate, success_rate,
        ttft_avg_ms, ttft_p99_ms, tpot_ms, e2e_p50_ms, e2e_p99_ms,
        prompt_toks, gen_toks, toks_per_s,
        kv_cache_usage_pct, cpu_cache_usage_pct, prefix_cache_hit_pct,
        gpu_util_pct, gpu_mem_used_mib, gpu_mem_total_mib, gpu_temp_c,
        cost_per_mtok, engine_meta_json)

checkups(id, ts, model, health_score, payload_json)   -- payload=诊断结果
```

## 7. 规则引擎约定（重要）

- 规则 = {id, name, level(warn|critical|info), window, condition(表达式/阈值来自 YAML), suggestion, evidence_fields[]}；
- **先校准后告警**：首次部署建议用 `check --baseline` 模式跑 1 天正常负载，输出建议阈值；
- 每条建议必须可验证：引擎输出时附带"复测命令/实验指引"（v0.2 提供实验对比页）；
- 规则评估幂等、可单测：fixtures 里放 vLLM/SGLang 各一份真实样例 metrics 文本。

## 8. 与已有开源项目的差异（竞争定位）

| 项目 | 做了什么 | 它没做的（我们的位置） |
|---|---|---|
| vllmstat / vllm-monitor / Grafana LLM dashboard | 实时面板/图 | 只显示指标，不给"结论+建议+实验" |
| vllm-cost-meter | vLLM 成本记账 | 只算钱，不解释"为什么贵、怎么改" |
| LLMKube | K8s 编排+部署+看板 | 偏编排运维，绑定 K8s |
| Inference AIOps (MCP) | vLLM 集群 root-cause + 扩缩容 | 起步阶段、绑定 K8s 集群场景 |
| Langfuse/Helicone | 应用层 trace/成本 | 看不到引擎内部 |

**差异化一句话**：别人给你"图和数据"，inferdiag 给你"结论、下一步动作和验证方法"；别人要 K8s，inferdiag 单机一条命令就能跑（个人/小团队门槛最低）。

## 9. 演进路线（与求职/参赛/商业化对齐）

- v0.1（4–6 周）：采集+Mock+规则+CLI+Web+成本 → GitHub 开源（Apache-2.0）；
- v0.2：实验对比、AI 人话总结 → 写技术博客/即刻引流，收 10–20 个真实使用者；
- v0.3：MCP Server + 昇腾/国产卡指标适配 → 投昇腾 C4-AI / Model Agent 类开发者大赛；
- v0.4：多实例、时序库升级 → 开始接"私有化交付服务商"（商业化第一站，付费方明确）；
- 求职叙事：README 一句话故事 + 架构文档 + 规则库 = 简历第 1 条项目经历；开发中给 vLLM/SGLang 提的 PR = 开源贡献记录。

## 10. 安全与隐私

- 默认零遥测、全本地；只读引擎公开 metrics，不读请求内容；
- 采集器支持 token auth；Web 面板 v0 默认仅监听 localhost（`--host` 可开，需自行加认证）；
- LLM 总结功能默认关闭；开启后仅发送"脱敏后的诊断 JSON"（不含业务文本）。
