// inferdiag dashboard —— 无依赖原生 JS：实时轮询 + 多尺度曲线
"use strict";

// EF 终端配色：主黄 #fff44f / 冷青 #8fd8ff / 告警红 #ff7979 / 薄荷 #b3e55e
const COLORS = ["#fff44f", "#8fd8ff", "#ff7979", "#b3e55e", "#ffb020", "#c9a0ff"];
const METRIC_META = {
  kv_cache_usage_pct: "KV cache %",
  ttft_p50_ms: "TTFT p50(ms)",
  ttft_p99_ms: "TTFT p99(ms)",
  e2e_p99_ms: "E2E p99(ms)",
  num_running: "运行中请求",
  num_waiting: "等待请求",
};
const LEVEL_CN = { critical: "严重", warning: "警告", info: "提示" };
const SERIES_KEYS = ["kv_cache_usage_pct", "ttft_p50_ms", "e2e_p99_ms", "num_running"];
const POLL_MS = 1000; // 轮询间隔：1 秒实时刷新

let seriesCache = { t: [], series: {} };
let lastUpdateAt = 0;

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
  return r.json();
}

function esc(v) {
  return v === null || v === undefined || v === "" ? "–" : v;
}
function fmt(v) {
  if (v === null || v === undefined) return "–";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

function renderFindings(findings) {
  const el = document.getElementById("findings");
  if (!findings || findings.length === 0) {
    el.innerHTML = '<span class="ok">✓ 未发现明显问题</span>';
    return;
  }
  el.innerHTML = findings
    .map((f) => {
      const ev = Object.entries(f.evidence || {}).map(([k, v]) => k + "=" + fmt(v)).join("，");
      return `<div class="finding">
        <div><span class="badge ${f.level}">${LEVEL_CN[f.level] || f.level}</span>
        <span class="name">${f.rule_id} ${esc(f.name)}</span></div>
        <div class="sug">💡 ${esc(f.suggestion)}</div>
        ${ev ? `<div class="ev">证据: ${ev}</div>` : ""}
      </div>`;
    })
    .join("");
}

function renderMetrics(snapshot) {
  const el = document.getElementById("metrics");
  const items = [
    ["运行/等待", fmt(snapshot?.num_running) + " / " + fmt(snapshot?.num_waiting)],
    ["KV cache", fmt(snapshot?.kv_cache_usage_pct) + "%"],
    ["TTFT p50/p99", fmt(snapshot?.ttft_p50_ms) + " / " + fmt(snapshot?.ttft_p99_ms) + " ms"],
    ["TPOT", fmt(snapshot?.tpot_ms) + " ms"],
    ["E2E p50/p99", fmt(snapshot?.e2e_p50_ms) + " / " + fmt(snapshot?.e2e_p99_ms) + " ms"],
    ["到达率", fmt(snapshot?.requests_success_rate) + " req/s"],
    ["前缀命中", fmt(snapshot?.prefix_cache_hit_pct) + "%"],
    ["生成速率", fmt(snapshot?.generation_tokens_rate) + " tok/s"],
  ];
  el.innerHTML = items
    .map(([k, v]) => `<div class="m"><div class="v">${v}</div><div class="k">${k}</div></div>`)
    .join("");
}

function drawChart() {
  const canvas = document.getElementById("chart");
  const legend = document.getElementById("legend");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 600;
  const h = 140;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const n = seriesCache.t.length;
  if (n < 2) {
    legend.textContent = "数据不足（≥2 个采样点才画曲线）";
    return;
  }
  const keys = SERIES_KEYS.filter(
    (k) => (seriesCache.series[k] || []).some((v) => v !== null && v !== undefined)
  );
  if (keys.length === 0) {
    legend.textContent = "暂无曲线数据";
    return;
  }

  const padT = 6, padB = 4, padX = 4;

  // 每条曲线独立 min/max 归一化（量纲差异大，共轴会把小值压扁）
  const ext = {};
  for (const k of keys) {
    const vals = seriesCache.series[k].filter((v) => v !== null && v !== undefined);
    let mn = Math.min(...vals), mx = Math.max(...vals);
    if (mx === mn) { mx += 1; mn = Math.max(0, mn - 1); }
    ext[k] = [mn, mx];
  }

  keys.forEach((k, ki) => {
    const vals = seriesCache.series[k];
    const [mn, mx] = ext[k];
    const toY = (v) => padT + (1 - (v - mn) / (mx - mn)) * (h - padT - padB);
    ctx.strokeStyle = COLORS[ki % COLORS.length];
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    let pen = false;
    vals.forEach((v, i) => {
      const x = padX + (i / Math.max(1, n - 1)) * (w - padX * 2);
      if (v === null || v === undefined) { pen = false; return; }  // 断点
      const y = toY(v);
      if (!pen) { ctx.moveTo(x, y); pen = true; } else { ctx.lineTo(x, y); }
    });
    ctx.stroke();
  });

  legend.innerHTML = keys
    .map((k, i) => `<span style="color:${COLORS[i % COLORS.length]}">■ ${METRIC_META[k] || k}</span>`)
    .join("&nbsp;&nbsp;");
}

async function refresh() {
  const err = document.getElementById("err");
  try {
    const o = await getJSON("/api/overview?window=120");
    const age = lastUpdateAt ? Math.round((Date.now() - lastUpdateAt) / 1000) : 0;
    const collecting = o.collecting ? "· 实时采集中" : "";
    document.getElementById("meta").textContent =
      `样本 ${o.sample_count} 条 · 窗口 ${o.window_seconds}s${collecting} · ${new Date().toLocaleTimeString()}（每秒自动刷新）`;

    const scoreEl = document.getElementById("score");
    scoreEl.textContent = o.score;
    scoreEl.style.color = o.score >= 90 ? "#fff44f" : o.score >= 60 ? "#ffb020" : "#ff7979";
    document.getElementById("scoreNote").textContent = o.score >= 90 ? "状态良好" : o.score >= 60 ? "需要关注" : "存在严重问题";

    renderFindings(o.findings);
    renderMetrics(o.metrics);
    lastUpdateAt = Date.now();
    err.textContent = "";

    const s = await getJSON("/api/series?limit=90&metrics=" + SERIES_KEYS.join(","));
    seriesCache = s;
    document.getElementById("nPts").textContent = s.t.length;
    drawChart();
  } catch (e) {
    err.textContent = "连接失败: " + e.message + "（请确认已运行 uv run inferdiag serve）";
  }
}

refresh();
setInterval(refresh, POLL_MS);

// ---------- 一键演示压测 ----------
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : "{}" });
  return r.json();
}

async function demoStart(workers, requests, maxTokens) {
  const st = document.getElementById("demoStatus");
  try {
    const d = await postJSON("/api/demo/start", { workers, requests, max_tokens: maxTokens });
    st.textContent = d.ok ? "压测启动中…" : "启动失败: " + (d.error || "?");
  } catch (e) { st.textContent = "请求失败: " + e.message; }
}

async function demoStop() {
  try { await postJSON("/api/demo/stop", {}); } catch (e) { /* 忽略 */ }
}

async function renderDemo() {
  const el = document.getElementById("demoStatus");
  const btns = ["dLight", "dMed", "dHeavy", "dStop"];
  try {
    const d = await getJSON("/api/demo/status");
    const active = d.active === true || (d.stop === false);
    btns.slice(0, 3).forEach((id) => { document.getElementById(id).disabled = active; });
    document.getElementById("dStop").disabled = !active;
    if (active) {
      el.textContent = `压测进行中 · 成功 ${d.ok} / 失败 ${d.err}${d.model ? " · " + d.model : ""}`;
    } else {
      const idle = d.ok === 0 && d.err === 0 ? "空闲" : `已结束 · 成功 ${d.ok} / 失败 ${d.err}`;
      el.textContent = d.engine_base ? idle : "未连接引擎（serve 需加 -m）";
    }
  } catch (e) { /* 看板主轮询报错会单独提示 */ }
}

setInterval(renderDemo, 1000);
renderDemo();

// ---------- 一键检测：进度实时展示 + 完成后渲染本次报告 ----------
let scanRenderedId = 0;

function fmtNum(v) {
  if (v === null || v === undefined) return "–";
  return typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(2)) : String(v);
}

function renderScanReport(r) {
  const box = document.getElementById("scanResultBody");
  if (!r || !r.findings) return;
  const scoreColor = r.score >= 90 ? "#fff44f" : r.score >= 60 ? "#ffb020" : "#ff7979";
  const findings = r.findings.length
    ? r.findings.map((f) => {
        const ev = Object.entries(f.evidence || {}).map(([k, v]) => k + "=" + fmtNum(v)).join("，");
        const lv = { critical: "严重", warning: "警告", info: "提示" }[f.level] || f.level;
        return `<div class="finding">
          <div><span class="badge ${f.level}">${lv}</span><span class="name">${f.rule_id} ${f.name}</span></div>
          <div class="ev">证据: ${ev}</div>
          <div class="sug">💡 ${f.suggestion}</div></div>`;
      }).join("")
    : '<span class="ok">✓ 未发现明显问题</span>';

  const items = [
    ["KV cache", fmtNum(r.metrics.kv_cache_usage_pct) + "%"],
    ["TTFT p50/p99", fmtNum(r.metrics.ttft_p50_ms) + " / " + fmtNum(r.metrics.ttft_p99_ms) + " ms"],
    ["E2E p50/p99", fmtNum(r.metrics.e2e_p50_ms) + " / " + fmtNum(r.metrics.e2e_p99_ms) + " ms"],
    ["运行/等待", fmtNum(r.metrics.num_running) + " / " + fmtNum(r.metrics.num_waiting)],
    ["到达率", fmtNum(r.metrics.requests_success_rate) + " req/s"],
    ["前缀命中", fmtNum(r.metrics.prefix_cache_hit_pct) + "%"],
  ];
  box.innerHTML = `
    <div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap">
      <div style="font-size:44px;font-weight:900;color:${scoreColor}">${r.score}</div>
      <div class="sub">健康分 / 100 · 样本 ${r.sample_count} 条 · 窗口 ${r.window_seconds}s
      <br>检测时间：${new Date(r.generated_at * 1000).toLocaleString()}</div>
    </div>
    <div style="margin-top:12px">${findings}</div>
    <div class="metrics">${items.map(([k, v]) => `<div class="m"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("")}</div>`;
  document.getElementById("scanResult").style.display = "block";
  document.getElementById("scanResult").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function scanStart() {
  try {
    const d = await postJSON("/api/scan/start", { duration: 30 });
    if (!d.ok) {
      document.getElementById("scanState").textContent = "启动失败：" + (d.error || "?");
      return;
    }
    document.getElementById("btnScan").disabled = true;
    document.getElementById("btnScanStop").style.display = "inline-block";
    document.getElementById("scanState").textContent = "检测进行中…";
  } catch (e) {
    document.getElementById("scanState").textContent = "请求失败：" + e.message;
  }
}

async function scanStop() {
  try { await postJSON("/api/scan/stop", {}); } catch (e) { /* 忽略 */ }
}

async function scanTick() {
  const st = document.getElementById("scanState");
  try {
    const s = await getJSON("/api/scan/status");
    const logEl = document.getElementById("scanLog");
    logEl.textContent = s.log.length ? s.log.slice(-14).join("\n") : logEl.textContent;

    if (s.running) {
      st.textContent = `检测中 ${s.step}/${s.duration} · 已用 ${s.elapsed}s`;
      st.className = "scanState";
      document.getElementById("btnScan").disabled = true;
      document.getElementById("btnScanStop").style.display = "inline-block";
      document.getElementById("scanBar").style.width = (s.duration ? (s.step / s.duration) * 100 : 0) + "%";
      document.getElementById("scanResult").style.display = "none";
      return;
    }
    document.getElementById("btnScan").disabled = false;
    document.getElementById("btnScanStop").style.display = "none";
    if (s.done && s.report && s.report.generated_at !== scanRenderedId) {
      scanRenderedId = s.report.generated_at;
      st.textContent = "检测完成";
      renderScanReport(s.report);
    } else if (!s.done && s.step === 0) {
      st.textContent = s.engine_base ? "未开始：点击按钮自动采集并实时显示进度" : "未连接引擎（serve 需加 -m）";
      st.className = "scanState idle";
    } else if (s.done) {
      st.textContent = "检测完成（点击可再次检测）";
    }
  } catch (e) { /* 忽略瞬时错误 */ }
}

setInterval(scanTick, 1000);
scanTick();
