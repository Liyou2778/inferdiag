# inferdiag 验收测试清单（User Acceptance Test）

> 以"使用者"视角验证：诊断是否可信、边界是否可靠、普通人是否看得懂。
> 用途：开发验收、发版前回归、简历/比赛的"经过 X 项验收"证据。
> 配套：`uv run inferdiag check/report`、`scripts/serve_mock_metrics.py`、`scripts/pressure_test.py`。

## T1 核心价值：病 / 健康能否分清
- [ ] 终端 A：`python scripts/serve_mock_metrics.py --mode stress`（模拟"生病"：KV 97%、排队 40、抢占频繁）
- [ ] 终端 B：采集后 `check` → 健康分应很低（stress 档 ≈9/100），报告点名 KV 打满/排队/TTFT 高
- [ ] 换 `--mode normal` 重采 → 健康分应 ≈100，显示"未发现明显问题"
- **判定**：两类状态结论截然不同，且 stress 报告问题与注入故障一一对应。

## T2 证据链可复算
- [ ] 每条 finding 带 `(证据: metric=值)`，用 `rules_v0.yaml` 的阈值手工复算能对上
- **判定**：无"空口诊断"。

## T3 阈值可配置
- [ ] 改 `rules/rules_v0.yaml`（如 R2 `value: 30→999`）→ `check` 结果随之变化，改回即恢复
- **判定**：规则是配置驱动，非写死。

## T4 引擎掉线不崩溃
- [ ] `collect` 运行中杀掉 mock/引擎 → 终端 B 打印 `[warn] scrape failed` 后继续，不退出
- **判定**：监控对象挂了，工具不自杀。

## T5 无数据时优雅降级
- [ ] `check --db data/空库.db` → 不报错、提示样本不足，绝不误导性输出"健康 100 分"
- **判定**：宁可不诊断，不可乱诊断。

## T6 结果一致性
- [ ] 同一份库连跑 3 次 `check --window 60` → 分数与触发规则一致
- **判定**：无随机抖动。

## T7 数据累积正确
- [ ] 查询：`select count(*), min(ts), max(ts) from samples` → 行数增长、时间戳有序
- [ ] 计数器修复回归：vLLM 真实引擎下 `requests_success_total`（按 finished_reason 拆分）应被**求和**（16 条请求→16）
- **判定**：计数型指标多序列求和正确（曾踩坑：只取最后一条序列导致恒为 0）。

## T8 报告可读性
- [ ] `uv run inferdiag report` 详细版 / Web 仪表盘，找一位不懂代码的人盲测："看完能否说出服务哪里有问题、先改什么"
- **判定**：能 → 文案合格。

## T9 真实引擎闭环（RTX 4060 / vLLM 0.8.5 / Qwen3B-AWQ 实测记录）
- [ ] 空闲窗口：kv≈0、running=0 → R10(空转, info) 触发，其余干净
- [ ] 压测窗口：TTFT/TPOT/E2E 出现真实毫秒数；prefix 命中率与引擎日志一致
- [ ] 负载后尾巴窗口：R10 **不**误报（子窗口 idle 判定生效）
- **判定**：真实数据 + 校准基线（docs/calibration.md）一致。

## T10 一键演示（Web）
- [ ] `serve -m <引擎/metrics>` 后页面顶部出现 轻/中/重/停止 按钮
- [ ] 点"中"→ 曲线实时跳动、状态显示"压测进行中 · 成功 N/失败 M"
- [ ] 非实时模式（未加 -m）点按钮 → 明确报错提示，不崩溃
- **判定**：演示闭环可复现。

---

### 复测命令速查
```bash
uv run inferdiag collect --url <engine>/metrics --interval 2 --seconds 60 --db data/t.db
uv run inferdiag check  --db data/t.db --window 60
uv run inferdiag report --db data/t.db --window 60
uv run inferdiag export --db data/t.db --window 60 -f md -o docs/sample-report
uv run pytest -q          # 自动回归 15 项
```
