# inferdiag 使用说明

本文档面向使用者与部署者，说明如何安装、接入数据源、使用 CLI 与 Web 仪表盘、配置规则与阈值，以及常见问题的处理。阅读前建议先看 `README.md` 了解项目定位。

## 1. 环境要求

- Python 3.10+（开发与工具运行）。
- 采集对象：暴露 Prometheus 文本格式 `/metrics` 的推理引擎（vLLM / SGLang）。引擎可在 Windows（Mock 源）、WSL2 / Linux 虚拟机 / 云 GPU（真实引擎）中运行；inferdiag 本体跨平台。
- 磁盘：SQLite 单库，每分钟 60 条样本（1s 间隔）约占用 < 1 MB，可长期运行。

## 2. 安装

```bash
git clone https://github.com/Liyou2778/inferdiag.git
cd inferdiag
uv sync --all-extras      # 推荐；等价于 pip install -e ".[dev]"
```

依赖：httpx、prometheus-client、fastapi、uvicorn、typer、pydantic、pyyaml；dev 依赖：pytest、ruff。

## 3. 数据接入

### 3.1 Mock 指标源（开发 / 无引擎）

```bash
python scripts/serve_mock_metrics.py --port 8001 --mode normal
# 可选 --mode stress：模拟 KV 打满、排队、抢占，用于触发规则演示
```

### 3.2 真实引擎

以 vLLM 为例（引擎须运行于 Linux 环境，见 8.4）：

```bash
# 引擎侧（WSL2 / Linux）
vllm serve Qwen/Qwen2.5-3B-Instruct-AWQ \
  --port 8000 --max-model-len 4096 --gpu-memory-utilization 0.8
```

inferdiag 只读取引擎的 `/metrics` 端点，不要求部署 Prometheus。启动后验证：

```bash
curl http://localhost:8000/metrics | head
```

指标字段以实际返回为准——不同版本命名存在差异，解析层会做归一化（见 5.3）。

## 4. CLI

```
inferdiag collect  --url <metrics> [--db 路径] [--interval 秒] [--seconds 秒] [--engine auto|vllm|sglang]
inferdiag check    [--db 路径] [--window 秒]     # 对最近窗口聚合后执行规则
inferdiag report   [--db 路径] [--window 秒]     # 详细报告（含关键指标快照）
inferdiag export   [--db 路径] [--window 秒] -f json|md -o 输出路径
inferdiag serve    [--db 路径] [--port 8080] [-m <metrics>] [--collect-interval 秒]
```

参数默认值：`--url http://127.0.0.1:8001/metrics`、`--db data/inferdiag.db`、`check/report/export --window 120`。

### 4.1 典型工作流

```bash
# 1) 采集 60 秒（2s 间隔）
uv run inferdiag collect --url http://localhost:8000/metrics --interval 2 --seconds 60

# 2) 体检
uv run inferdiag check --window 60
uv run inferdiag report --window 60

# 3) 导出
uv run inferdiag export --window 60 -f md -o docs/sample-report
uv run inferdiag export --window 60 -f json -o report.json
```

`check` 输出：健康分、样本数、触发规则列表（严重度 + 证据值 + 建议）。
`report` 在此基础上追加窗口聚合的关键指标与注意项。

### 4.2 采集行为说明

- 采集为前台循环，按 `--interval` 轮询并写入 SQLite；`--seconds <= 0` 表示无限采集。
- 抓取失败只打印警告并继续，不会中断进程（适用于引擎重启场景）。
- 空窗期：时间窗内无样本时自动回退到最近的 30 条样本，避免误报"无数据"。

## 5. 诊断模型与配置

### 5.1 数据流

```
/metrics 文本 → 解析(parse_prometheus_text) → 归一化(Sample 28 字段)
    → SQLite 样本表 → 窗口聚合(max/mean/rate) → 规则引擎 → 报告
```

### 5.2 窗口聚合指标（规则输入）

量规取窗口最大值/均值，计数型按首末差值折算速率。主要键：

- `num_running` / `num_waiting`（最大）
- `kv_cache_usage_pct` / `prefix_cache_hit_pct`
- `ttft_p50_ms` / `ttft_p99_ms` / `tpot_ms` / `e2e_p50_ms` / `e2e_p99_ms`（均值）
- `preemptions_rate` / `requests_success_rate` / `prompt_tokens_rate` / `generation_tokens_rate`（速率）
- `idle_no_activity`（布尔）：窗口前半段无活动且整窗无请求完成时置真，用于"低峰空转"判定

### 5.3 归一化注意点

- 计数型指标常按标签拆成多条序列（如 `request_success_total` 按 `finished_reason` 拆分）：解析时按裸名**求和**。
- 延迟指标在不同版本可能是 Summary（带 `quantile` 标签）或 Histogram（`_bucket`）：两者均支持，Histogram 分位数取所在 bucket 上界近似。

### 5.4 规则配置（`inferdiag/rules/rules_v0.yaml`）

规则结构：

```yaml
rules:
  - id: R1
    name: <规则名>
    level: critical        # critical | warning | info
    conditions:
      - {metric: <指标>, op: <ge|gt|le|lt|eq>, value: <阈值>}
    suggestion: <建议文本>
```

语义：

- 一条规则的多个 condition 为"且"关系；
- 任一 condition 引用的指标在窗口数据中缺失（`null`）时，该规则自动跳过，不产生误报；
- 阈值默认按特定硬件/模型标定（见 `docs/calibration.md`），更换部署环境前必须重新标定。

规则清单（R1–R14）：OOM 风险（KV≥95%）、队列积压、TTFT、TPOT、KV 容量规划、前缀命中率、吞吐偏低、成本、抢占、低峰空转、长尾延迟、PD 分离评估、量化评估、扩容建议。详细阈值与建议见文件本体。

### 5.5 阈值校准流程

1. 采集"正常负载基线"：`collect` 60–120s（空闲与常规负载各一段）；
2. 查看 `report` 中聚合数值；
3. 按"阈值 ≈ 正常负载峰值 × 1.5–2"修改 `rules_v0.yaml`；
4. 用 `scripts/pressure_test.py` 复测，确认正常负载不告警、异常负载能告警。

### 5.6 成本估算

`inferdiag/cost.py` 中 `PRICING` 默认 `None`（禁用）。配置单价表（每百万 token 价格）后，`check/report` 会附加窗口成本估算；未配置时报告明示"未配置单价表"。

## 6. Web 仪表盘

### 6.1 启动

```bash
# 实时模式：后台线程采集 + 看板每秒刷新
uv run inferdiag serve --db data/live.db -m http://localhost:8000/metrics --collect-interval 1
# 静态模式：只展示已有库（无 -m）
uv run inferdiag serve --db data/inferdiag.db --port 8080
```

默认监听 `127.0.0.1:8080`。跨主机访问需显式指定 `--host 0.0.0.0`，并自行增加认证（内置无鉴权）。

### 6.2 界面区域

- 健康分：90+ / 60–89 / <60 三档，颜色区分；
- 诊断建议：触发规则、证据值与建议；
- 指标卡片：当前快照关键值；
- 曲线：多指标时间序列，每条独立缩放；
- 一键压测（实时模式下可用）：轻/中/重三档向引擎发请求，状态行显示成功/失败计数。模型名从引擎 `/v1/models` 自动发现；静态模式点击会返回明确错误。

### 6.3 API

| 路径 | 说明 |
|---|---|
| `GET /api/overview?window=120` | 健康分 + findings + 聚合指标 + 最新样本 |
| `GET /api/series?limit=90&metrics=a,b` | 最近 N 条样本的时序 |
| `GET /api/health` | 服务状态、库行数、是否采集模式 |
| `GET/POST /api/demo/status|start|stop` | 一键压测控制 |

## 7. 测试

```bash
uv run pytest -q     # 15 项：解析、聚合、规则、Web API、边界
```

人工验收清单见 `docs/acceptance-test.md`。

## 8. 常见问题

**8.1 找不到 `uv` 命令**
安装目录未加入 PATH。Windows 下 `uv` 经 `pip install --user` 安装于 `%APPDATA%\Python\Python312\Scripts`；重启终端或手动将该目录加入用户 PATH。

**8.2 采集时报连接失败**
确认引擎在线且端口正确；`curl <url>` 应返回 Prometheus 文本。防火墙/容器网络需保证可达。采集循环本身会持续重试，不会退出。

**8.3 字段大量为 0 或缺失**
- 空闲时 KV/请求数天然为 0，属正常；
- 延迟类指标（TTFT/E2E）只在有请求完成后才更新——先产生负载再观察；
- 若某指标恒缺失，用 `curl` 对照实际 `/metrics` 字段名，必要时扩展 `collector/parse.py` 的映射。

**8.4 vLLM 在 WSL2 无法启动（历史问题归纳）**
- vLLM ≥0.28 在 WSL2 存在 UVA 不可用问题（`GPUModelRunnerV2`），建议使用 vLLM 0.8.x；
- `transformers` 需 <5（与 0.8.5 配套）；
- 模型权重下载失败：设置 `HF_ENDPOINT=https://hf-mirror.com`、`HF_HUB_DISABLE_XET=1`，或改用 ModelScope 下载后以本地路径启动。

**8.5 显存不足（`gpu-memory-utilization`）**
笔记本 GPU 同时被桌面占用时，空闲显存低于目标比例会启动失败；调低 `--gpu-memory-utilization`（0.7–0.8）并缩短 `--max-model-len`。

**8.6 看板一直"连接中"或空白**
- 确认访问端口为 8080（引擎在 8000）；
- 强制刷新（浏览器缓存旧 JS）；
- `serve` 需保持前台运行；
- 早期版本的静态文件挂载问题已修复：页面脚本路径为 `/static/app.js`。

**8.7 告警与预期不符**
阈值是部署相关的：按 5.5 重新标定；负载刚结束的窗口可能短暂触发 R10 的已修复，其余规则同样以窗口数据为准，必要时加长 `--window`。

**8.8 SQLite 相关**
Windows 下多线程访问已通过 `check_same_thread=False` + WAL 处理；若使用旧版代码或自行多进程写同一库，请改用独立库文件或接入外部时序库。

## 9. 数据与文件

- 库文件默认 `data/inferdiag.db`（已被 git 忽略）；表 `samples(ts, engine, payload)`，`payload` 为归一化样本 JSON。
- 常用脚本：`scripts/serve_mock_metrics.py`（Mock 源）、`scripts/pressure_test.py`（一次性压测）、`scripts/continuous_load.py`（持续轻负载）。
- 文档：`docs/architecture.md`、`docs/calibration.md`、`docs/acceptance-test.md`。

## 10. License 与免责

Apache-2.0。诊断建议仅供参考；修改生产配置前请在测试环境验证，作者不对误操作导致的损失负责。
