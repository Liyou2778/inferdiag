# inferdiag：从 0 到上线的全流程 Playbook

> 目标：8 周内完成 v0.1 开源发布 + 在线 Demo + 求职/比赛素材
> 环境：Windows 主机（产品开发）+ WSL2/VM 或云（引擎实验）+ RTX 4060 8GB（够用）
> 原则：先 Mock 后真实引擎；先 CLI 后 UI；每天至少一次 git commit；尽早公开仓库。

---

## Phase 0 · 环境准备（0.5 天）

| 事项 | 命令/操作 |
|---|---|
| 检查 git | `git --version`；没有则装 Git for Windows |
| 配置身份 | `git config --global user.name "你的名字"` / `git config --global user.email "邮箱"` |
| GitHub 账号 | 注册 github.com；`Settings → SSH and GPG keys` |
| 生成 SSH 密钥 | `ssh-keygen -t ed25519 -C "你的邮箱"`（一路回车）；把 `~/.ssh/id_ed25519.pub` 内容贴到 GitHub |
| 验证 | `ssh -T git@github.com` 出现 "Hi 用户名" 即成功 |
| Python | 安装 Python 3.11 或 3.12（python.org，勾选 Add to PATH）；`python --version` |
| uv（推荐） | `pip install uv`（或 `winget install astral-sh.uv`）|
| VS Code | 装 Python、Ruff 扩展 |

验收：终端里 git/ssh/python 全部可用。

---

## Phase 1 · 项目骨架 + 首次发布（1–2 天）

在你的工作目录（已有 README.md 和 docs/architecture.md）执行：

```bash
cd inferdiag
git init -b main
# 建 pyproject.toml（见下）、.gitignore、LICENSE
uv init --bare   # 或手写 pyproject.toml
uv add httpx prometheus-client fastapi uvicorn typer pydantic
uv add --dev pytest ruff
mkdir -p inferdiag/collector inferdiag/rules inferdiag/web/static mock scripts tests/fixtures docs
# 各包放 __init__.py；把 inferdiag/cli.py 先写一个 hello 命令
```

`pyproject.toml` 要点：
```toml
[project]
name = "inferdiag"
version = "0.1.0"
description = "LLM 推理体检报告生成器：诊断 vLLM/SGLang 的慢/贵/怎么改"
requires-python = ">=3.10"
dependencies = ["httpx", "prometheus-client", "fastapi", "uvicorn", "typer", "pydantic"]

[project.scripts]
inferdiag = "inferdiag.cli:app"

[project.optional-dependencies]
dev = ["pytest", "ruff"]
```

`.gitignore` 至少包含：`.venv/`、`__pycache__/`、`*.db`、`dist/`、`build/`、`.pytest_cache/`

然后立刻推到 GitHub（**早公开，养成习惯**）：
```bash
git add -A && git commit -m "chore: project skeleton (docs + structure)"
gh repo create inferdiag --public --source . --push   # 需装 gh；或网页建库后 git remote add + push
```

验收：`uv run inferdiag --help` 能跑；GitHub 仓库在线。

---

## Phase 2 · Mock 指标源 + 采集器（3–5 天，全程不需要 GPU）

按顺序做（每步可独立验证）：

1. **`scripts/serve_mock_metrics.py`**：起一个 HTTP 服务，在 `/metrics` 返回 Prometheus 文本格式的假指标（`vllm:num_requests_running`、`vllm:gpu_cache_usage_perc`、`vllm:time_to_first_token_seconds` 等），数值随时间波动（模拟"早上正常、下午打满、夜间空闲"），提供两个档位：normal / stress。
   - 验收：`python scripts/serve_mock_metrics.py --port 8001` 后浏览器打开 `http://127.0.0.1:8001/metrics` 能看到指标文本。
2. **`collector/models.py`**：定义 28 项字段的 dataclass/pydantic 模型（对照采集清单文档）。
3. **`collector/parse.py`**：用 `prometheus_client.parser.text_string_to_metric_families` 解析文本 → 归一化到字段模型（vLLM/SGLang 命名差异在此层抹平；**字段名以你实际看到为准**）。
4. **`collector/scrape.py`**：httpx 定时抓取（默认 10s），超时/重试/优雅退出。
5. **`store.py`**：SQLite 建 `samples` 表（字段同 models），写入与查询最近 N 条。
6. **`cli.py`** 的 `collect` 子命令把 2–5 串起来。

```bash
uv run inferdiag collect --url http://127.0.0.1:8001/metrics --seconds 60
uv run python -c "import sqlite3;print(sqlite3.connect('inferdiag.db').execute('select count(*) from samples').fetchone())"
```
验收：DB 里开始累积 samples 行。

---

## Phase 3 · 规则引擎 + 报告 + 成本（4–7 天）

1. **`rules/rules_v0.yaml`**：R1–R14 全量，每条含 {id, name, level, window, threshold, suggestion, evidence_fields}。
2. **`rules/engine.py`**：取最近时间窗（如 5 分钟）聚合（max/mean/p99）→ 逐条评估 → 输出 `CheckupItem{rule_id, level, evidence, suggestion}`。
3. **`report.py`**：健康分（100 − Σ权重×违规）+ 瓶颈排序 + 建议清单，输出 JSON。
4. **`cost.py`**：单价表（config 里配模型/输入/输出 token 价、卡型/时价）→ 每 token 成本；R10 的"空转浪费金额"（利用率为 0 的时段 × 卡价）。
5. **CLI 扩展**：`check`（对最近数据出报告）、`report`（人类可读终端报告）。
6. **测试**：`tests/fixtures/` 放 vLLM 与 SGLang 各一份样例 metrics 文本；每条规则至少一个"触发样例"和一个"不触发样例"。

```bash
uv run inferdiag check --model mock
uv run inferdiag report
uv run pytest
```
验收：在 stress 档的 mock 数据下，`report` 能触发 ≥5 条规则并给出建议；`pytest` 全绿。

---

## Phase 4 · Web 仪表盘（3–5 天）

1. **`web/app.py`**：FastAPI。路由：
   - `GET /` → 返回单页前端；
   - `GET /api/samples/latest` → 最近指标；
   - `GET /api/checkups` → 历史诊断；
   - `GET /api/report` → 最新报告。
2. **`web/static/index.html` + `app.js`**：原生 JS 单页（表格 + 简单折线，可用 CDN Chart.js），显示：当前健康分、瓶颈列表、最近指标快照、成本估算。
3. CLI `serve` 子命令：`uvicorn` 启动。

```bash
uv run inferdiag serve   # 打开 http://127.0.0.1:8000
```
验收：浏览器能看到"会跳数字的仪表盘 + 能点开的体检报告"。

---

## Phase 5 · 打磨与质量（2–3 天）

- `uv run ruff check . && uv run ruff format .`；
- `uv run pytest` 全绿；补 README 截图；
- 处理边界：引擎掉线（显示"离线"而非报错）、空数据、字段缺失；
- **录一条 ≤3 分钟 demo 视频**：起 mock(stress) → `collect` → `report` 触发几条规则 → 浏览器看板；这是求职与比赛的核心素材；
- 把文档链齐：README 顶部换真实截图和徽章。

验收：陌生人照 README 能在 10 分钟内跑起来（可先找你同学测一次）。

---

## Phase 6 · 开源发布 v0.1.0（1 天）

```bash
git tag v0.1.0
git push origin main --tags
```
- GitHub 仓库页：写清 description（中英各一句）、Topics：`vllm` `sglang` `llm-observability` `llmops` `ai-infra` `cost-optimization` `monitoring`；
- 建 Release v0.1.0：附 changelog + demo 视频链接 + 截图；
- （加分）加 GitHub Actions：`.github/workflows/ci.yml` 跑 `pytest`（简历上写"CI 全绿"有说服力）。

验收：GitHub 首页一眼能看懂"这是什么、解决什么、怎么跑"。

---

## Phase 7 · 在线 Demo 上线（1–2 天，三档选一）

**档 A（免费/临时演示，先做这个）**——cloudflared 隧道：
```bash
winget install cloudflare.cloudflared        # 或官网下载
cloudflared tunnel --url http://127.0.0.1:8000
# 输出 https://xxxx.trycloudflare.com —— 把链接发出去就能给人演示
```

**档 B（长期 Demo，学生云服务器）**——推荐，简历/比赛要长期在线：
- 腾讯云/阿里云"轻量应用服务器"学生价（约 ¥100–300/年），系统选 Ubuntu 22.04；买香港区可免 ICP 备案（国内区绑域名需备案，直接用 IP 访问则不需要）；
- 服务器内：
```bash
sudo apt update && sudo apt install -y docker.io docker-compose
git clone <你的仓库> && cd inferdiag
# 写 Dockerfile（python:3.11-slim + pip install .）和 docker-compose.yml：
#   服务1: mock 源(8001)；服务2: inferdiag serve(8000→宿主80)
docker compose up -d
```
- 浏览器访问 `http://<服务器IP>` 即产品 Demo。

**档 C**：Render / Railway / Fly.io 免费层托管 FastAPI（界面更好看，但多数要国外信用卡，且国内访问不稳——当作备选）。

验收：一个长期在线、能被评委/面试官打开的 Demo 链接。

---

## Phase 8 · 引流 + 求职 + 比赛素材（持续）

- **发 3 篇内容**（V2EX/掘金/知乎专栏/公众号任选）：
  1. "我做了个给 vLLM 看病的开源工具"（产品故事 + Demo 链接）；
  2. 架构文（改自 docs/architecture.md）："面板之上的大脑：LLM 推理可观测缺的那一层"；
  3. 深度文："KV cache 才是你推理账单的大头"（蹭热点、引流量）。
- 收集 10–20 个真实 issue/用户反馈 → 选 2–3 个做进 v0.2（这就是比赛要的"落地证据"）；
- 简历第 1 条写 inferdiag（一句话故事 + star/用户数 + 架构亮点），GitHub 置顶；
- v0.3 加昇腾适配 → 投昇腾 C4-AI / Model Agent 类大赛（技术赛道用 Demo 视频直接参赛）。

---

## 总时间表与验收清单

| 阶段 | 周期 | 关键验收 |
|---|---|---|
| P0 环境 | 0.5 天 | git/ssh/python/uv 可用 |
| P1 骨架 | 1–2 天 | `inferdiag --help` 可跑，仓库在线 |
| P2 Mock+采集 | 3–5 天 | DB 里 samples 在累积 |
| P3 规则+报告 | 4–7 天 | stress 数据下 report 触发 ≥5 规则，pytest 绿 |
| P4 Web | 3–5 天 | 浏览器能看到仪表盘 |
| P5 打磨 | 2–3 天 | demo 视频录好，README 截图就位 |
| P6 发布 | 1 天 | v0.1.0 Release 在线 |
| P7 上线 Demo | 1–2 天 | 公网 Demo 链接可开 |
| P8 引流/求职 | 持续 | 10+ 用户反馈，简历更新 |

**避坑清单**：别跳 Mock 直接连引擎（你还没装好 vLLM，会卡住）；别过早美化 UI（先 CLI 跑通逻辑）；指标字段名一律以实际输出为准；每天 commit；Demo 视频 ≤3 分钟且"现场能跑"。
