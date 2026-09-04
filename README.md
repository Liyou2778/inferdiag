# inferdiag · LLM 推理"体检报告生成器"

> **Inference Diagnostics for vLLM / SGLang**
> 5 分钟告诉你：你的 LLM 服务**为什么慢、为什么贵、该怎么改、改了有没有用**。
> 不做第 N 个监控面板，做"面板之上的那一层大脑"。

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)]()
<!-- 截图占位：加一张 dashboard 截图后放这里 -->

---

## 为什么做它（痛点）

部署了开源模型（vLLM / SGLang）的团队和个人，现在只有三样工具：

1. Grafana/Prometheus 自己画图 —— **只有图，没有结论**；
2. 引擎日志 —— 全是术语，OOM/TTFT 高要靠人肉猜；
3. Excel 记成本 —— 只知道花了钱，不知道**钱为什么没换来吞吐**。

`inferdiag` 补的是这三样之间缺失的一环：把引擎指标翻译成**人话诊断 + 可执行建议 + 改前/改后对比实验**。

> 我们不重复造"监控面板"——这类轮子 GitHub 上已经很多（vllmstat / vllm-monitor / Grafana 官方 LLM dashboard 等）。
> 参考/差异化分析见 [`docs/architecture.md`](docs/architecture.md)。

---

## 功能（v0.1 范围）

### 已规划核心能力
| 能力 | 说明 | 状态 |
|---|---|---|
| 指标采集 | 定时抓取 vLLM / SGLang 的 Prometheus `/metrics` 端点 | v0.1 |
| 指标回放/Mock | 内置假数据源 + 离线样例回放，**无 GPU 也能完整开发调试** | v0.1 |
| 诊断规则引擎 | R1–R14：OOM / TTFT 高 / TPOT 慢 / KV 利用率高 / 前缀命中低 / 每 token 成本异常 / 夜间空转烧钱…… | v0.1 |
| 体检报告 | 每次检查输出：健康分 + 瓶颈清单 + 建议动作（阈值 YAML 可配） | v0.1 |
| 成本视图 | 单价表配置 → 每 token 成本、浪费算力金额 | v0.1 |
| Web 看板 | 概览 + 历史体检记录 + 建议列表（轻量单页） | v0.1 |
| CLI | `collect / check / report / serve` | v0.1 |
| 实验对比 | "改前/改后同一负载"的指标对比页 | v0.2 |
| AI 人话总结 | 可选接入 OpenAI 兼容 API（DeepSeek/豆包），把报告翻成大白话 | v0.2 |
| MCP Server | 让任意 AI 助手能查询集群健康与建议 | v0.3 |
| 国产卡适配 | 昇腾等环境适配（面向开发者大赛） | v0.3+ |

### 明确不做（v0 阶段）
- ❌ 不替代监控面板（那是 Grafana 的活）；
- ❌ 不拦截/代理推理流量（那是网关的活）；
- ❌ 不内置压测工具（只输出"建议用 xx 命令复测"）；
- ❌ 不采集业务数据（只读指标，数据不出你机器）。

---

## 快速开始

> 环境要求：Python 3.10+。**开发与调试不需要 GPU**（用 Mock 源即可），连真实引擎时需 vLLM/SGLang 暴露 `/metrics`（在 WSL2 / Linux 虚拟机 / 云 GPU 中运行引擎均可，Windows 上直接跑产品本体）。

```bash
git clone <your-repo-url> && cd inferdiag
pip install -e .            # 或 uv sync

# 方式一：无 GPU，先用内置假指标源体验完整流程（推荐先跑这个）
python scripts/serve_mock_metrics.py --port 8001
inferdiag collect --url http://localhost:8001/metrics
inferdiag check
inferdiag serve             # 打开 http://localhost:8000

# 方式二：连真实引擎（引擎地址换成你的 vLLM/SGLang）
inferdiag collect --url http://<engine-host>:8000/metrics
inferdiag check --model qwen3b
```

一条命令生成人话报告：

```bash
inferdiag report            # 终端版体检报告
```

---

### 实时监控（边采边看，每秒刷新）

```bash
uv run inferdiag serve --db data/live.db -m http://localhost:8000/metrics --collect-interval 1
# 打开 http://127.0.0.1:8080 —— 每 1s 自动刷新；加负载时曲线实时跳动
```

配套工具：`scripts/pressure_test.py`（一次性并发压测）、`scripts/continuous_load.py`（持续轻负载演示）。
校准方法与实测基线（RTX 4060 + Qwen3B-AWQ）见 [`docs/calibration.md`](docs/calibration.md)。

## 架构（速览）

```
┌──────────┐   HTTP /metrics   ┌────────────────────────────────────────┐
│ vLLM /   │ ────────────────► │ inferdiag                              │
│ SGLang   │                   │  collector(抓取+解析) → store(SQLite)   │
└──────────┘                   │        ↓                               │
┌──────────┐                   │  rules engine(R1–R14, YAML阈值)         │
│ Mock 源   │ ──(开发/测试)──►  │        ↓                               │
└──────────┘                   │  report(健康分+瓶颈+建议) → cost(成本)  │
                               │        ↓                               │
                               │  CLI / Web Dashboard / (v0.2: LLM总结) │
                               └────────────────────────────────────────┘
```

- **采集**：只做"Prometheus 文本格式解析 + 定时抓取"，不依赖 Prometheus 本体；
- **存储**：v0 用 SQLite（零部署）；规模上来后换 Timescale/ClickHouse（接口已抽象）；
- **规则**：阈值全部放 `rules_v0.yaml`，改配置即改诊断逻辑；
- **报告**：规则输出结构化结果（JSON），Web/CLI/LLM 总结都是同一份数据的不同渲染。

完整模块设计与数据流见 [`docs/architecture.md`](docs/architecture.md)。

---

## 诊断规则一览（v0）

| # | 症状 | 一句话建议方向 |
|---|---|---|
| R1 | OOM/崩溃 | 降 max-model-len / 开前缀缓存 / 换量化 / 减并发 |
| R2 | TTFT 整体高 | 队列过载 → 调 max_num_seqs / 扩容 / PD 分离 |
| R3 | 长请求 TTFT 高 | 队头阻塞 → prefix caching / 长请求分池 |
| R4 | TPOT 慢 | 算力受限 → 换卡/量化/蒸馏（或查调度/通信） |
| R5 | KV 利用率长期 >90% | 会话清理 / 上下文上限 / 扩容信号 |
| R6 | 前缀命中 <20% | 开自动前缀缓存 / 规范 prompt 结构 |
| R7 | 每 token 成本异常高 | batch 太小 → 调并发与批处理参数 |
| R8 | 吞吐低于预期 | 同上 + 检查 gpu-memory-utilization 配额 |
| R9 | 抢占频繁 | 减并发 / 加 KV 配额 / 长请求限流 |
| R10 | 低峰空转烧钱 | 缩容 / 按需实例 / 换小模型，量化浪费金额 |
| R11 | p99 长尾抖动 | 超时 / 队列优先级 / 长请求隔离 |
| R12 | 该不该上 PD 分离 | 按 prefill 占比 × TTFT 权重给收益预估 |
| R13 | 该不该换量化/小模型 | 显存紧张 + 精度容忍 → 给对比实验建议 |
| R14 | 单实例排队该不该扩 | 扩容 + 网关路由，扩容后自动对比验证 |

> ⚠️ 阈值为 v0 初始值，**必须先在你的环境跑一轮正常负载基线再启用告警**（阈值 ≈ 基线 × 1.5~2）。

---

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.10+ | 生态成熟、你已熟练 |
| 采集/解析 | httpx + prometheus_client 文本解析 | 零额外依赖抓 Prometheus 格式 |
| 存储 | SQLite（stdlib）→ 预留时序库接口 | v0 零部署，先跑通 |
| Web/API | FastAPI + 原生单页(vanilla JS) | 依赖少、易打包进面试演示 |
| CLI | Typer | 顺手好用 |
| LLM 总结(可选) | OpenAI 兼容 API（DeepSeek 等） | 国内可用、便宜；默认关闭 |

---

## 路线图

- **v0.1（≈4–6 周）**：采集 + Mock + 规则引擎 R1–R14 + CLI + 轻量 Web + 成本视图 → 开源发布
- **v0.2**：实验对比（改前/改后）、AI 人话总结、报告导出
- **v0.3**：MCP Server；国产卡(昇腾)指标适配 → 对接开发者大赛/社区
- **v0.4**：多实例/K8s 场景；时序存储升级

---

## 目录结构

```
inferdiag/
├── README.md
├── LICENSE
├── pyproject.toml
├── inferdiag/
│   ├── cli.py                 # collect/check/report/serve
│   ├── config.py              # 全局配置(单价表、频率…)
│   ├── collector/
│   │   ├── scrape.py          # HTTP 抓取
│   │   ├── parse.py           # Prometheus 文本 → 内部模型
│   │   └── models.py          # 28 项指标字段模型
│   ├── store.py               # SQLite 落库(接口可换)
│   ├── rules/
│   │   ├── engine.py          # 规则执行器
│   │   ├── registry.py        # 规则注册
│   │   └── rules_v0.yaml      # R1–R14 阈值(可改)
│   ├── report.py              # 结构化报告/健康分
│   ├── cost.py                # 成本估算
│   └── web/
│       ├── app.py             # FastAPI
│       └── static/            # 单页前端
├── mock/                      # 假指标生成(无 GPU 开发用)
├── scripts/
│   └── serve_mock_metrics.py  # 模拟 /metrics 服务
├── tests/
│   └── fixtures/              # vLLM/SGLang 真实样例指标文本
└── docs/
    └── architecture.md
```

---

## 贡献 / 交流

- 找问题：提 [Issues]（贴上你的 `/metrics` 输出片段最好）；
- 想共建：看 [v0.1 里程碑]，从"指标字段映射表补全"或"给 R1–R14 补测试样例"入手；
- 灵感来源与差异分析见 [`docs/architecture.md`](docs/architecture.md)（对比 vllmstat / vllm-monitor / Grafana LLM dashboard / vllm-cost-meter / LLMKube / Inference AIOps）。

---

## 文档

- [架构设计（目标/模块/差异定位）](docs/architecture.md)
- [阈值校准记录（真实硬件基线）](docs/calibration.md)
- [验收测试清单（10 项 UAT）](docs/acceptance-test.md)

## License

Apache-2.0。与任何推理引擎厂商无隶属关系。

## 免责声明

诊断建议仅供参考：**修改生产配置前请先在测试环境跑改前/改后对比实验**，本项目不对任何误操作导致的损失负责。
