# inferdiag 优化清单与发展路线

> 基于 2026-09 的 v0.2.0 现状评估制定。现状：核心链路（采集→解析→存储→规则→报告→CLI/Web）完整闭环，
> 15 项测试全绿、CI 齐备、阈值经 RTX 4060 + vLLM 0.8.5 + Qwen2.5-3B-AWQ 实测标定。
> 主要短板：SGLang 支持名不副实、建议不可验证、配置硬编码、无发布渠道。

---

## 一、优化执行清单（按顺序做）

### 阶段 0：小改动，一个晚上清完（每件 ≤ 30 分钟）

- [ ] **README 放截图/GIF**。用现成 `data/stress.db` 起 `uv run inferdiag serve --db data/stress.db` 截仪表盘；
      录一段"一键压测 → 曲线跳起 → 健康分下降 → 给出建议"的 GIF（Windows 推荐 ScreenToGif）。
      存 `docs/assets/`，嵌在 README 第一段下方，并加 CI/License/Python 三枚徽章。
- [ ] **修正文档矛盾**：
  - `cli.py:3` docstring"serve 在 P4 提供"→ 删除；
  - `report.py` notes"阈值为 v0 初始值"→ 改为"RTX 4060 + Qwen2.5-3B 基线标定值，换环境请重新校准"；
  - `architecture.md` Non-Goals"不内置压测工具"→ 改为"正式压测走外部脚本，仪表盘内置压测仅用于演示"。
- [ ] **CI 加 ruff**：`.github/workflows/ci.yml` 增加 `uv run ruff check .` 步骤；先本地清零再提交。
- [ ] **pyproject.toml 补元数据**：`license = "Apache-2.0"` + classifiers（发 PyPI 的前置条件）。
- [ ] **README 功能表**：SGLang 标注"实验性"（待阶段 1 落地后升级）。

### 阶段 1：可信度修复（1–2 天）

- [ ] **SGLang 字段级支持**
  1. 本机起真实源：`pip install "sglang[all]"` →
     `python -m sglang.launch_server --model-path Qwen/Qwen2.5-1.5B-Instruct-AWQ --port 30000 --enable-metrics`；
  2. `curl http://127.0.0.1:30000/metrics > tests/fixtures/sglang_sample.txt`（空载 + 压测后各一份，记录 SGLang 版本号）；
  3. `collector/parse.py` 加裸名别名表并在 `normalize()` 识别引擎后重写：
     ```python
     SGLANG_ALIASES = {
         "num_running_reqs": "num_requests_running",
         "num_queue_reqs": "num_requests_waiting",
         "token_usage": "gpu_cache_usage_perc",           # 0~1，走现有 ×100 路径
         "inter_token_latency_seconds": "time_per_output_token_seconds",
     }
     ```
     注意 `sglang:cache_hit_rate` 是 0~1 的 **gauge**（非 hits/queries counter），
     在 `normalize()` 里直接 `s.prefix_cache_hit_pct = cache_hit_rate * 100`，
     `store.window_metrics` 已有 gauge 均值兜底，天然兼容；
  4. `tests/test_parse.py` 补 sglang fixture 用例（断言关键字段非 None）；
  5. 验收：`uv run pytest -q` 全绿，README 状态升级为"SGLang（已在 x.y.z 字段级验证）"。
     已知坑：不同版本存在冒号/下划线命名不一致（sglang#20752）、multiproc 模式下指标发现差异。
- [ ] **修 R8 占位规则**：在 `cost.py` 回填 `metrics["cost_per_mtok"]`（混合单价 = 窗口成本 ÷ 窗口 token 总数 × 1e6，
      阈值注释写清"按实际账单修改"）；或直接删除 R8。当前"列着但永远跳过"最差。

### 阶段 2：工程加固（2–3 天）

- [ ] **SQLite 线程锁**：`store.py` 加 `self._lock = threading.RLock()`
      （RLock 因 `window_metrics` 内部再调 `window_samples`），
      `insert_sample / count / latest / window_samples / close` 全部包锁。
      验收：`serve -m` 边采边刷半小时无 "database is locked"。
- [ ] **数据保留策略**：`store.py` 加 `purge_before(ts)`（DELETE + commit）；
      CLI 加 `--retention-hours`（默认 168），采集循环每小时清理一次。
- [ ] **overview 降耗**：`/api/overview` 每秒全表 `COUNT(*)` 改为 O(1) 的 `SELECT MAX(rowid) FROM samples`。
      验收：50 万行时 overview 响应 < 50ms，库体积稳定。
- [ ] **配置外置**：
  - `rules/engine.py`：`load_rules(path=None)` 加路径参数；CLI `check/report/export/serve` 加 `--rules` 透传到 `build_report`；
  - `cost.py`：单价改环境变量优先：`INFERDIAG_PRICE_IN` / `INFERDIAG_PRICE_OUT`（Docker 也要用）。
- [ ] **cache-bust 自动化**：`index.html` 的 `?v=20260904d` 去掉，`web/app.py` 的 `index()` 改为
      读 `app.js` 的 mtime 注入 `?v=`（HTMLResponse + 字符串替换）。

### 阶段 3：差异化功能（3–5 天，按序）

- [ ] **复测命令**：YAML 每条规则加可选 `verify` 字段（如 R2：
      `verify: python scripts/pressure_test.py --workers 8 --seconds 60 && inferdiag check --window 120`）；
      `engine.evaluate()` 透传 `rule.get("verify")`；CLI / Web / export 三个渲染端各加一行"复测： …"。
      验收：触发 R2 的场景，报告里能复制粘贴一条命令直接验证。
- [ ] **实验对比（改前/改后）**：复用 `export` 产 JSON，新增 `inferdiag compare before.json after.json`：
      对 ttft_p50/p99、tpot、e2e_p99、kv、num_waiting、吞吐、前缀命中、score 逐项算差值与百分比，
      按"延迟类下降=改善 / 吞吐命中率上升=改善"标注，末尾输出一句结论。
      这是 README"计划中"划掉、诊断闭环补全的一步。
- [ ] **基线校准自动化**：新增 `inferdiag calibrate --db xxx --window 3600`：
      读库对核心指标算 p95，输出建议阈值（×1.5~2）并生成 `my_rules.yaml` 骨架，
      配合 `--rules` 参数实现"跑一天基线 → 得到自己的规则文件"（即 architecture.md §7 的 `check --baseline`）。

### 阶段 4：发布与传播（1–2 天，功能稳定后）

- [ ] **Docker**：根目录 Dockerfile（python:3.12-slim + `pip install .` + ENTRYPOINT serve，
      `--host 0.0.0.0 --db /data/inferdiag.db`，单价走 `INFERDIAG_PRICE_*` 环境变量）；
      README 给 Linux `--network host` 一行命令；GitHub Actions 发 ghcr.io。
- [ ] **PyPI**：先查名可用性；用 Trusted Publisher（PyPI 关联仓库与 workflow，免 token）：
      workflow 跑 `uv build` + `pypa/gh-action-pypi-publish`；完成后 README 安装段第一行 `pip install inferdiag`。

---

## 二、技术演进路线（对齐三条发展路径）

| 版本 | 内容 | 服务的目标 |
|---|---|---|
| v0.3 | SGLang 真支持、复测命令、实验对比、calibrate、**MCP Server**、**昇腾指标采集**（npu-smi / MindIE 的 Prometheus 端点） | 求职作品集定稿 + 昇腾类比赛报名 |
| v0.4 | 多实例聚合视图、Prometheus remote_write 集成（可选输出而非替代）、规则包（长文本/RAG/Agent 场景预设） | 企业试用 / 私有化交付探测 |
| v0.5 | 集群级调度建议、A/B 实验自动化、国产卡认证清单 | 商业化 |

**MCP Server 优先做**：现有 FastAPI API 包一层 MCP 协议即可（官方 Python SDK），
效果是任何 Agent（Claude 等）可以直接"询问"引擎健康并拿到结构化诊断——
对简历（2026 年 Agent 方向岗位高度相关）、比赛（现场演示 LLM 自动诊断）、商业化（AIOps 叙事）三线同时加分，且工作量小。

---

## 三、三条发展路径

### 路径 A：求职/作品集（主线，确定性最高）

项目本身就是 AI Infra 面试弹药：R1–R14 每条规则对应一道面试题
（KV cache 与抢占、continuous batching、prefix cache、PD 分离、量化、histogram 分位数）。

1. **简历条目要有数字**：独立开发 inferdiag——vLLM/SGLang 推理诊断工具，
   采集→诊断→建议→验证闭环；R1–R14 规则经真实 vLLM 0.8.5 + Qwen2.5-3B 实测标定；
   15 项单测 + CI；PyPI/ghcr 发布；star 数。可补一个实验数字：
   "定位一次推理劣化根因的时间从 ~30 分钟人工看面板降到 1 分钟读报告"。
2. **写 2–3 篇技术博客**（掘金/知乎）：
   - 《给 vLLM 做体检报告生成器：R1–R14 规则的实测标定过程》
   - 《Prometheus histogram 分位数的坑：为什么 p99 可能高估数倍》
   - 《60fps 监控曲线：采样 1s、绘制 60fps 的外推平滑方案》
3. **上游贡献 ≥1 个 PR**（vLLM 或 SGLang，文档/小 bug 即可）——"contributor to vLLM"比任何自研项目都硬。
4. **补强项**：阶段 1 的 SGLang（面试官一问就穿帮的点）、阶段 4 的 PyPI/Docker（工程成熟度信号）、MCP（Agent 岗位加分）。

投入 2–4 周，ROI 最高且确定。

### 路径 B：创业比赛（放大器，与 A 复用物料）

适配赛道：中国国际大学生创新大赛（原互联网+）、挑战杯、省级双创赛、华为昇腾 C4 等开发者大赛。

- **强项**：痛点真实可量化；"一键压测→健康分跳水→给建议→一键复测恢复"的现场演示极直观；
  差异化一句话清晰（监控给图，我们给结论+验证闭环）；有真实标定数据（不是 PPT 项目）；
  昇腾适配完成后有国产化叙事。
- **评委必问的弱项与应对**：
  - 壁垒？——诚实答：规则库与校准数据是数据壁垒、开源社区是生态壁垒；承认 observability 大厂可复制，讲先发+生态+数据积累。
  - 市场？——讲私有化部署渗透率与推理运维成本占比，用试用者访谈数据支撑。
  - 有用户吗？——比赛前拿 3–5 个真实试用者 + 2 封意向/试用证明。
  - 团队？——单人短板明显，找 1–2 名队友（市场/路演方向）。
- **物料清单**：10 页 BP、8 分钟路演稿、3 分钟 demo 视频、成本节省测算（如 R10 空转缩容省 X%）。
- **节奏**：昇腾类比赛契合度最高，等 v0.3（国产卡适配完成）再报；通用双创赛可先用 v0.3 前的版本练手。

### 路径 C：创业（设验证闸门，不提前 all-in）

诚实判断：

- "诊断工具"单点是 feature 不是公司。可行形态是开源核心 + 企业版（多实例/集群/国产卡认证/合规）+ 集成商渠道（Grafana、PostHog 式 open core 路线），周期长且依赖社区规模。
- 竞品风险：Datadog/Grafana/Arize 等 observability 厂商向 LLM Infra 延伸是顺手的；引擎厂商也可能内置。
- **中国市场的真实缝隙**：国产算力 + 大模型私有化部署的运维工具链——企业在昇腾/海光上私有化部署
  Qwen/DeepSeek，运维能力跟不上、SaaS 用不了（数据不出域），集成商需要可交付的配套工具。
  "体检报告"可以直接成为集成商交付物的一部分（对应 architecture.md 的 P3 persona，付费方明确）。

**验证闸门（达到才考虑 all-in）**：≥10 个真实使用者（非朋友）、≥3 家企业试用、≥1 家愿意付费试点。
在此之前：用路径 A 拿确定收益、用路径 B 放大与攒资源、项目以业余节奏养。

**推荐总路线：A 为主线，B 复用 A 的物料，C 挂闸门观察。**
理由：A 确定性最高且与 B 完全复用；A 的投递与面试反馈本身就是 C 的低成本市场调研。

---

## 四、里程碑

| 时间 | 目标 |
|---|---|
| 2 周 | 阶段 0–1 完成；README 有 GIF；SGLang 字段级落地 |
| 1 个月 | 阶段 2–3 完成；v0.3 发版（PyPI + ghcr）；第一篇博客；MCP Server demo |
| 2 个月 | 10 个真实用户反馈（issue/群/邮件）；简历投递启动；比赛报名 |
| 3 个月 | 上游 PR ≥1；完成一次路演；企业试用线索 ≥3 |

---

## 参考

- SGLang Production Metrics（官方指标清单，需 `--enable-metrics`）：
  https://docs.sglang.ai/references/production_metrics.html
- SGLang Prometheus Metrics Guide：https://kuncoro.io/blog/sglang-prometheus-metrics-guide/
- sglang#20752 指标命名不一致问题：https://github.com/sgl-project/sglang/issues/20752
