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
