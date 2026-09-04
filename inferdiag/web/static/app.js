// inferdiag dashboard —— 流畅实时渲染
// 数据：overview 1s 轮询（数字/建议，仅变化时更新 DOM）
//       曲线数据 300ms 轮询；绘制用 requestAnimationFrame(60fps) 时间轴滚动
"use strict";

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
const OVERVIEW_MS = 1000;
const SERIES_MS = 300;
const MAX_POINTS = 150;

let seriesCache = { t: [], series: {} };
let seriesSig = "";
let overviewKey = "";
let legendSig = "";
let lastError = "";

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : "{}" });
  return r.json();
}

function esc(v) {
  return v === null || v === undefined || v === "" ? "–" : String(v);
}
function fmt(v) {
  if (v === null || v === undefined) return "–";
  return typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(2)) : String(v);
}

// ---------- 概览（每秒） ----------
function renderFindings(findings) {
  const el = document.getElementById("findings");
  if (!findings || findings.length === 0) {
    el.innerHTML = '<span class="ok">✓ 未发现明显问题</span>';
    return;
  }
  el.innerHTML = findings.map((f) => {
    const ev = Object.entries(f.evidence || {}).map(([k, v]) => k + "=" + fmt(v)).join("，");
    return `<div class="finding">
      <div><span class="badge ${f.level}">${LEVEL_CN[f.level] || f.level}</span>
      <span class="name">${f.rule_id} ${esc(f.name)}</span></div>
      <div class="sug">💡 ${esc(f.suggestion)}</div>
      ${ev ? `<div class="ev">证据: ${ev}</div>` : ""}
    </div>`;
  }).join("");
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
  el.innerHTML = items.map(([k, v]) =>
    `<div class="m"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");
}

function fmtCompact(v) {
  if (v === null || v === undefined || isNaN(v)) return "–";
  const n = Math.floor(v);
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}

function updateFooter(o) {
  const lat = o.latest || {};
  const m = o.metrics || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set("fReq", fmtCompact(lat.requests_success_total));
  set("fSamples", fmt(o.sample_count));
  set("fTtft", fmt(m.ttft_p50_ms));
  set("fRate", fmt(m.generation_tokens_rate));
  set("fCache", (m.prefix_cache_hit_pct == null ? "–" : fmt(m.prefix_cache_hit_pct) + "%"));
  set("fIn", fmtCompact(lat.prompt_tokens_total));
  set("fOut", fmtCompact(lat.generation_tokens_total));
}

async function refreshOverview() {
  const errEl = document.getElementById("err");
  try {
    const o = await getJSON("/api/overview?window=120");
    const collecting = o.collecting ? "· 实时采集中" : "";
    document.getElementById("meta").textContent =
      `样本 ${o.sample_count} 条 · 窗口 ${o.window_seconds}s${collecting} · ${new Date().toLocaleTimeString()}（实时）`;
    updateFooter(o);

    // 只在数据真正变化时重绘 DOM，减少闪烁
    const key = JSON.stringify([o.score, o.findings, o.metrics]);
    if (key !== overviewKey) {
      overviewKey = key;
      const scoreEl = document.getElementById("score");
      scoreEl.textContent = o.score;
      scoreEl.style.color = o.score >= 90 ? "#fff44f" : o.score >= 60 ? "#ffb020" : "#ff7979";
      document.getElementById("scoreNote").textContent =
        o.score >= 90 ? "状态良好" : o.score >= 60 ? "需要关注" : "存在严重问题";
      renderFindings(o.findings);
      renderMetrics(o.metrics);
    }
    errEl.textContent = lastError = "";
  } catch (e) {
    if (e.message !== lastError) {
      lastError = e.message;
      errEl.textContent = "连接失败: " + e.message;
    }
  }
}

// ---------- 曲线数据（300ms） ----------
async function loadSeries() {
  try {
    const s = await getJSON("/api/series?limit=" + MAX_POINTS + "&metrics=" + SERIES_KEYS.join(","));
    seriesCache = s;
    const sig = s.t.length + ":" + SERIES_KEYS.map((k) => (s.series[k] || []).length).join(",");
    if (sig !== seriesSig) {
      seriesSig = sig;
      document.getElementById("nPts").textContent = s.t.length;
    }
  } catch (e) { /* 概览轮询会提示错误 */ }
}

// ---------- 60fps 时间轴滚动绘制 ----------
function drawChart() {
  const canvas = document.getElementById("chart");
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 600;
  const h = 150;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const t = seriesCache.t;
  const n = t.length;
  const padX = 6, padT = 8, padB = 4;
  if (n < 2) {
    drawLegend(["数据不足（≥2 点才绘制）"]);
    return;
  }
  const now = Date.now() / 1000;

  // 只在首尾点变化时重算图例（避免每帧重建）
  const keys = SERIES_KEYS.filter((k) => (seriesCache.series[k] || []).some((v) => v !== null));
  const sig = keys.join("|") + "|" + n;
  if (sig !== legendSig) {
    legendSig = sig;
    drawLegend(keys.map((k) => `<span style="color:${COLORS[SERIES_KEYS.indexOf(k) % COLORS.length]}">■ ${METRIC_META[k] || k}</span>`));
  }

  // 平滑：最后采样点之后，按最近斜率外推一个"实时端点"，随墙钟连续前移，
  // 直到下一个真实采样到达再锚定 —— 消除"一格一格跳"。
  function buildSeries(ki) {
    const vals = seriesCache.series[ki];
    if (!vals) return null;
    const idx = [];
    const xs = [];
    const ys = [];
    let prevReal = null, prevPrevReal = null; // (t, v)
    let j = -1;
    for (let i = 0; i < n; i++) {
      const v = vals[i];
      if (v === null || v === undefined) continue;
      j++;
      const tk = t[i];
      idx.push(j);
      xs.push(tk);
      ys.push(v);
      prevPrevReal = prevReal;
      prevReal = { t: tk, v };
    }
    if (ys.length < 2) return null;

    // y 范围按真实点标定
    let mn = Math.min(...ys), mx = Math.max(...ys);
    if (mx === mn) { mx += 1; mn = Math.max(0, mn - 1); }
    const yOf = (v) => Math.min(Math.max(padT, padT + (1 - (v - mn) / (mx - mn)) * (h - padT - padB)), h - padB);

    // 追加实时外推端点（60fps 每帧前移）
    if (prevReal && now > prevReal.t) {
      let tipV = prevReal.v;
      if (prevPrevReal && prevReal.t - prevPrevReal.t > 1e-6) {
        const slope = (prevReal.v - prevPrevReal.v) / (prevReal.t - prevPrevReal.t);
        const maxStep = Math.abs(prevReal.v - prevPrevReal.v); // 最多再延伸一个同样量级的变化
        const ext = slope * (now - prevReal.t);
        tipV = prevReal.v + Math.max(-maxStep, Math.min(maxStep, ext));
      }
      xs.push(now);
      ys.push(tipV);
    }
    return { xs, ys, yOf };
  }

  const t0 = t[0];
  const tEnd = Math.max(t[n - 1], now);
  const span = Math.max(1e-6, tEnd - t0);
  const xOf = (ts) => padX + ((ts - t0) / span) * (w - padX * 2);

  keys.forEach((k) => {
    const series = buildSeries(k);
    if (!series) return;
    ctx.strokeStyle = COLORS[SERIES_KEYS.indexOf(k) % COLORS.length];
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    let pen = false;
    for (let i = 0; i < series.xs.length; i++) {
      const x = xOf(series.xs[i]);
      const y = series.yOf(series.ys[i]);
      if (!pen) { ctx.moveTo(x, y); pen = true; } else { ctx.lineTo(x, y); }
    }
    ctx.stroke();
  });
}

function drawLegend(parts) {
  const legend = document.getElementById("legend");
  if (typeof parts[0] === "string" && parts[0].indexOf("■") === -1) {
    legend.textContent = parts.join("  ");
  } else {
    legend.innerHTML = parts.join("&nbsp;&nbsp;");
  }
}

// ---------- 一键演示压测 ----------
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
  try {
    const d = await getJSON("/api/demo/status");
    const active = d.active === true || (d.stop === false);
    ["dLight", "dMed", "dHeavy"].forEach((id) => { document.getElementById(id).disabled = active; });
    document.getElementById("dStop").disabled = !active;
    el.textContent = active
      ? `压测进行中 · 成功 ${d.ok} / 失败 ${d.err}`
      : (d.ok === 0 && d.err === 0 ? "空闲" : `已结束 · 成功 ${d.ok} / 失败 ${d.err}`);
  } catch (e) { /* 忽略 */ }
}

// ---------- 一键检测 ----------
let scanRenderedAt = 0;
function renderScanReport(r) {
  const box = document.getElementById("scanResultBody");
  const scoreColor = r.score >= 90 ? "#fff44f" : r.score >= 60 ? "#ffb020" : "#ff7979";
  const findings = r.findings.length
    ? r.findings.map((f) => {
        const ev = Object.entries(f.evidence || {}).map(([k, v]) => k + "=" + fmt(v)).join("，");
        const lv = LEVEL_CN[f.level] || f.level;
        return `<div class="finding">
          <div><span class="badge ${f.level}">${lv}</span><span class="name">${f.rule_id} ${f.name}</span></div>
          <div class="ev">证据: ${ev}</div>
          <div class="sug">💡 ${f.suggestion}</div></div>`;
      }).join("")
    : '<span class="ok">✓ 未发现明显问题</span>';
  const items = [
    ["KV cache", fmt(r.metrics.kv_cache_usage_pct) + "%"],
    ["TTFT p50/p99", fmt(r.metrics.ttft_p50_ms) + " / " + fmt(r.metrics.ttft_p99_ms) + " ms"],
    ["E2E p50/p99", fmt(r.metrics.e2e_p50_ms) + " / " + fmt(r.metrics.e2e_p99_ms) + " ms"],
    ["运行/等待", fmt(r.metrics.num_running) + " / " + fmt(r.metrics.num_waiting)],
    ["到达率", fmt(r.metrics.requests_success_rate) + " req/s"],
    ["前缀命中", fmt(r.metrics.prefix_cache_hit_pct) + "%"],
  ];
  box.innerHTML = `
    <div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap">
      <div style="font-size:44px;font-weight:900;color:${scoreColor}">${r.score}</div>
      <div class="sub">健康分 / 100 · 样本 ${r.sample_count} 条 · 窗口 ${r.window_seconds}s
      <br>检测时间：${new Date(r.generated_at * 1000).toLocaleString()}</div>
    </div>
    <div style="margin-top:12px">${findings}</div>
    <div class="metrics">${items.map(([k, v]) => `<div class="m"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("")}</div>`;
  const panel = document.getElementById("scanResult");
  panel.style.display = "block";
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
    if (s.log.length) logEl.textContent = s.log.slice(-14).join("\n");

    if (s.running) {
      st.textContent = `检测中 ${s.step}/${s.duration} · ${s.elapsed}s`;
      st.className = "scanState";
      document.getElementById("btnScan").disabled = true;
      document.getElementById("btnScanStop").style.display = "inline-block";
      document.getElementById("scanBar").style.width = (s.duration ? (s.step / s.duration) * 100 : 0) + "%";
      document.getElementById("scanResult").style.display = "none";
      return;
    }
    document.getElementById("btnScan").disabled = false;
    document.getElementById("btnScanStop").style.display = "none";
    if (s.done && s.report && s.report.generated_at !== scanRenderedAt) {
      scanRenderedAt = s.report.generated_at;
      renderScanReport(s.report);
      st.textContent = "检测完成（可再次点击）";
    } else if (s.done) {
      st.textContent = "检测完成（可再次点击）";
    } else {
      st.textContent = s.engine_base ? "未开始：点击后自动采集并实时显示进度" : "未连接引擎（serve 需加 -m）";
      st.className = "scanState idle";
    }
  } catch (e) { /* 忽略瞬时错误 */ }
}

// ---------- 启动 ----------
refreshOverview();
loadSeries();
setInterval(refreshOverview, OVERVIEW_MS);
setInterval(loadSeries, SERIES_MS);
setInterval(renderDemo, 1000);
renderDemo();
setInterval(scanTick, 1000);
scanTick();

function animationLoop() {
  drawChart();
  requestAnimationFrame(animationLoop);
}
requestAnimationFrame(animationLoop);
