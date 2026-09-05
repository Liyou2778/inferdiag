# inferdiag

面向 vLLM / SGLang 推理服务的诊断工具。读取引擎暴露的 Prometheus 指标，将其聚合并映射为可执行的诊断结论：服务当前处于何种状态、瓶颈在哪、哪些参数值得调整。

定位是监控体系的补充层：Grafana / Prometheus 负责指标展示，inferdiag 负责把指标变成结论。它不拦截流量、不替代监控面板、不采集业务数据。

安装、接入数据源、CLI / Web 用法、规则与阈值配置、故障排查见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。

## 功能

| 模块 | 说明 | 状态 |
|---|---|---|
| 指标采集 | 定时抓取引擎 `/metrics`（Prometheus 文本格式），vLLM 已验证；SGLang 实验性 | 可用 |
| 字段归一化 | 引擎命名差异在解析层统一；带标签计数器按序列求和；histogram 分位数估算 | 可用 |
| 诊断规则 | R1–R14 规则集，阈值由 YAML 配置，指标缺失的规则自动跳过 | 可用 |
| 体检报告 | 输出健康分（0–100）、触发规则、证据值与建议 | 可用 |
| Web 仪表盘 | 单页实时看板：健康分、诊断建议、指标卡片、曲线；1s 轮询 | 可用 |
| 演示负载 | 看板内置压测控制，自动发现引擎服务模型 | 可用 |
| CLI | `collect` `check` `report` `export` `serve` | 可用 |
| 实验对比（改前/改后） | 计划中 | 未实现 |
| LLM 报告总结 / MCP / 国产卡适配 | 计划中 | 未实现 |

## 安装

要求 Python 3.10+。

```bash
git clone https://github.com/Liyou2778/inferdiag.git
cd inferdiag
uv sync --all-extras        # 或 pip install -e ".[dev]"
```

## 快速开始

开发与调试不依赖 GPU：使用内置 Mock 指标源即可走通完整链路。

```bash
# 终端 1：启动模拟指标源
python scripts/serve_mock_metrics.py --port 8001 --mode stress

# 终端 2：采集并体检
uv run inferdiag collect --url http://127.0.0.1:8001/metrics --interval 2 --seconds 60
uv run inferdiag check --window 60      # 输出健康分与诊断建议
uv run inferdiag report --window 60     # 详细报告
```

连接真实引擎时，将 `--url` 指向引擎的 `/metrics` 端点即可（vLLM 默认 `http://<host>:8000/metrics`）。

### 实时监控

```bash
uv run inferdiag serve --db data/live.db -m http://localhost:8000/metrics --collect-interval 1
# 浏览器打开 http://127.0.0.1:8080
```

`-m` 指定引擎指标地址后，服务后台线程持续采集并写入本地库，看板每 1 秒刷新。未指定 `-m` 时以静态库模式运行（只展示已有数据）。

## CLI 参考

```
inferdiag collect  --url <metrics> [--db 路径] [--interval 秒] [--seconds 秒] [--engine auto|vllm|sglang]
inferdiag check    [--db 路径] [--window 秒]          # 对最近窗口聚合后执行规则
inferdiag report   [--db 路径] [--window 秒]          # 详细版（含关键指标快照）
inferdiag export   [--db 路径] [--window 秒] -f json|md -o 输出路径
inferdiag serve    [--db 路径] [--port 8080] [-m <metrics>] [--collect-interval 秒]
```

## 配置

- **诊断规则**：`inferdiag/rules/rules_v0.yaml`。每条规则由若干条件（指标、运算符、阈值）与建议文本组成。修改阈值后立即生效，无需重新编译。
- **阈值语义**：阈值代表"偏离正常负载的程度"。默认值按特定硬件/模型标定，部署到其它环境前应先采集基线并据此调整（校准方法见下）。
- **成本估算**：`inferdiag/cost.py` 中的 `PRICING` 未配置时为禁用状态；配置单价表后按窗口内 token 增量估算成本。

## 架构

```
引擎 /metrics ──> collector（抓取+解析+归一化）──> SQLite（时序样本）
                                              │
                                              v
                          窗口聚合（max/mean/rate）──> rules engine（YAML 阈值）
                                              │
                                              v
                            报告（健康分+findings）──> CLI / Web
```

- 解析层只依赖引擎自带的 Prometheus 端点，不要求部署 Prometheus 本体。
- 存储当前为单文件 SQLite（WAL 模式）。`SQLiteStore` 的查询接口独立，可替换为时序库。
- 规则引擎输入为"窗口聚合指标字典"，输出为结构化 finding 列表；CLI 与 Web 复用同一份输出。

设计取舍、数据模型与同类工具（vllmstat、vllm-monitor、Grafana LLM dashboard、vllm-cost-meter、LLMKube、Inference AIOps）的差异分析见 [docs/architecture.md](docs/architecture.md)。

## 实测基线

规则默认阈值基于以下环境标定（详见 [docs/calibration.md](docs/calibration.md)）：

- 硬件：RTX 4060 Laptop 8GB（WSL2）
- 引擎：vLLM 0.8.5，模型 Qwen2.5-3B-Instruct-AWQ，`--max-model-len 4096`

实测数据：空闲时 KV cache 占用约 0%；4 并发请求负载下 KV 峰值约 3%、TTFT p50 约 40 ms、E2E p99 约 15 s。换用其它硬件、模型或上下文长度后请重新执行校准流程（`scripts/pressure_test.py` + `collect`）。

## 测试

```bash
uv run pytest -q
```

覆盖：Prometheus 解析与归一化、histogram 分位数估算、计数器多序列求和、规则引擎触发/跳过逻辑、窗口聚合空转判定、Web API（含空库与未接引擎的边界）。

用户视角的验收清单见 [docs/acceptance-test.md](docs/acceptance-test.md)。

## 开发

新增诊断规则：

1. 在 `rules/rules_v0.yaml` 中追加 `{id, name, level, conditions, suggestion}`；
2. 在 `tests/` 下为规则补充"触发样例"与"不触发样例"；
3. 指标字段如需扩展，先改 `collector/models.py`，再在 `collector/parse.py` 增加字段映射，最后在 `store.window_metrics` 中聚合。

脚本工具：`scripts/serve_mock_metrics.py`（模拟指标源）、`scripts/pressure_test.py`（一次性并发压测）、`scripts/continuous_load.py`（持续轻负载）。

## 已知限制

- histogram 分位数为所在 bucket 的上界近似（vLLM 的 bucket 较粗）。
- 当前为单实例 SQLite 存储，未针对大规模多实例做设计。
- 窗口聚合不做时间子窗拆分；R10（低峰空转）通过"窗口前半段是否有活动"判定来规避负载结束后的误报，极端采样下仍可能不精确。

## 目录结构

```
inferdiag/
├── inferdiag/
│   ├── cli.py                 # CLI 入口
│   ├── store.py               # SQLite 存储 + 窗口聚合
│   ├── report.py              # 报告与健康分
│   ├── cost.py                # 成本估算（可选）
│   ├── collector/             # 抓取 / 解析 / 归一化
│   ├── rules/                 # 规则引擎 + rules_v0.yaml
│   └── web/                   # FastAPI 后端 + 单页前端
├── scripts/                   # mock 源与压测工具
├── tests/                     # 自动化测试
└── docs/                      # 架构、校准、验收文档
```

## License

Apache-2.0。详见 [LICENSE](LICENSE)。

诊断建议仅供参考；修改生产配置前请在测试环境先行验证。本项目与任何推理引擎厂商无隶属关系。
