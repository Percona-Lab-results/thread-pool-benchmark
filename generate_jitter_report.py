#!/usr/bin/env python3
"""
Scans benchmark_logs/<server>/<version>/ for
run<R>_<ROWS>_Tier<M>G_(tpoff|tp<S>_os<O>)_RW_<T>th.sysbench.txt files and
generates a self-contained interactive Plotly HTML report showing the
per-second throughput *jitter*: one box (with the individual per-second TPS
samples as a jittered point cloud) per configuration and client thread count,
similar to classic "throughput jitter" box plots.

The per-second samples come from the sysbench --report-interval=1 lines
("tps: 2060.92"); each configuration is downsampled to an evenly spaced
subset to keep the report small.

To stay readable (and responsive), the report refuses to draw more than
6 series at a time: narrow the selection if the warning shows.

Usage:
    python3 generate_jitter_report.py [--base-dir=benchmark_logs] [--output=<file>]
                                      [--max-threads=<n>] [--samples=<n>]
"""

import argparse
import json
import re
import sys
from pathlib import Path

FILENAME_RE = re.compile(
    r"^run(?P<run>\d+)_(?P<rows>\d+[KkMm]?)_Tier(?P<mem>\d+)G_"
    r"(?:tpoff|tp(?P<tp>\d+)_os(?P<os>\d+))_RW_(?P<threads>\d+)th\.sysbench\.txt$"
)
# Per-second report lines: "[ 887s ] thds: 40 tps: 2060.92 qps: ... lat (ms,95%): 27.66 ..."
TPS_SEC_RE = re.compile(r"\btps: ([0-9.]+)")
LAT_SEC_RE = re.compile(r"lat \(ms,95%\): ([0-9.]+)")


def downsample(values, n):
    """Evenly spaced subset of at most n values (preserves the time spread)."""
    if len(values) <= n:
        return values
    step = len(values) / n
    return [values[int(i * step)] for i in range(n)]


def scan_runs(base_dir: Path, max_threads: int, samples: int):
    """{(server version, mem, tp, os, threads) -> [per-second tps samples]}"""
    groups = {}
    for server_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        for version_dir in sorted(p for p in server_dir.iterdir() if p.is_dir()):
            for f in sorted(version_dir.glob("run*.sysbench.txt")):
                m = FILENAME_RE.match(f.name)
                if not m:
                    continue
                threads = int(m.group("threads"))
                if threads > max_threads:
                    continue
                text = f.read_text(errors="replace")
                vals = [float(v) for v in TPS_SEC_RE.findall(text)]
                lat_vals = [float(v) for v in LAT_SEC_RE.findall(text)]
                if not vals:
                    print(f"  NA result (skipped): {f}", file=sys.stderr)
                    continue
                tp = "off" if m.group("tp") is None else int(m.group("tp"))
                osub = None if m.group("os") is None else int(m.group("os"))
                key = (f"{server_dir.name} {version_dir.name}", int(m.group("mem")),
                       tp, osub, threads)
                g = groups.setdefault(key, {"samples": [], "lat": [], "runs": set(),
                                            "rows": m.group("rows")})
                g["samples"].extend(vals)
                g["lat"].extend(lat_vals)
                g["runs"].add(int(m.group("run")))

    records = []
    for (server, mem, tp, osub, threads), g in sorted(
            groups.items(), key=lambda kv: [str(x) for x in kv[0]]):
        records.append({
            "server": server, "mem_gb": mem, "tp": tp, "os": osub,
            "threads": threads,
            "rows": g["rows"],
            "runs": sorted(g["runs"]),
            "samples": [round(v, 1) for v in downsample(g["samples"], samples)],
            "lat": [round(v, 2) for v in downsample(g["lat"], samples)],
        })
    return records


def tp_sort_key(tp):
    return (0, 0) if tp == "off" else (1, tp)


def build_data_block(records):
    servers = sorted({r["server"] for r in records})
    mems = sorted({r["mem_gb"] for r in records})
    threads = sorted({r["threads"] for r in records})
    tps = sorted({r["tp"] for r in records}, key=tp_sort_key)
    osv = sorted({r["os"] for r in records if r["os"] is not None})
    block = (
        f"const RUNS = {json.dumps(records, separators=(',', ':'))};\n"
        f"const MEMS = {json.dumps(mems)};\n"
        f"const THREADS = {json.dumps(threads)};\n"
        f"const TP_SIZES = {json.dumps(tps)};\n"
        f"const OS_VALUES = {json.dumps(osv)};"
    )
    return block, servers, mems, threads, tps, osv


TEMPLATE = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Sysbench Throughput Jitter</title>
  <script>
    function loadPlotly(cb) {
      var s = document.createElement('script');
      s.src = 'https://cdn.plot.ly/plotly-2.30.0.min.js';
      s.onload = cb;
      s.onerror = function() {
        var s2 = document.createElement('script');
        s2.src = 'https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.30.0/plotly.min.js';
        s2.onload = cb;
        s2.onerror = function() {
          document.getElementById('chart').innerHTML = '<p style="color:red;padding:20px">Could not load Plotly. Please open this file directly in a browser.</p>';
        };
        document.head.appendChild(s2);
      };
      document.head.appendChild(s);
    }
  </script>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 18px; }
    .wrap { display: grid; grid-template-columns: 320px 1fr; gap: 18px; align-items: start; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 14px; }
    label { display:block; font-weight: 600; margin: 10px 0 6px; }
    select { width: 100%; padding: 8px; border-radius: 10px; border: 1px solid #ccc; }
    select[multiple] { height: 120px; }
    #serverSel { height: 80px; }
    .hint { color: #555; font-size: 12px; line-height: 1.4; margin-top: 8px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .btnrow { display:flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
    button { padding: 8px 10px; border-radius: 10px; border: 1px solid #ccc; background: #f7f7f7; cursor: pointer; }
    button:hover { background: #eee; }
    #chart { height: 780px; }
    /* Plotly marks legend entries of hidden (legendonly) traces with an
       inline opacity of 0.5; fade them further so the disabled state is
       clearly visible */
    #chart .legend g.traces[style*="opacity: 0.5"] { opacity: 0.2 !important; }
    #tableView { max-height: 780px; overflow: auto; }
    #tableView table { border-collapse: collapse; width: 100%; font-family: monospace; font-size: 13px; }
    #tableView th, #tableView td { border: 1px solid #ccc; padding: 5px 10px; text-align: right; white-space: nowrap; }
    #tableView th { background: #e8eaf0; color: #555; position: sticky; top: 0; }
    #tableView th.name, #tableView td.name { text-align: left; }
    #tableView .caption { font-family: system-ui, sans-serif; font-size: 13px; font-weight: 700; color: #333; margin: 4px 0 8px; }
    #tableView .pct { font-size: 10px; opacity: 0.75; float: left; margin-right: 8px; }
    #warn {
      display: none; margin-bottom: 10px; padding: 10px 14px;
      background: #fff3cd; border: 1px solid #ffe08a; border-radius: 9px;
      color: #7a5d00; font-size: 13px;
    }

    /* Detail section (downloads + InnoDB charts) shown under the graph */
    #detailSection { margin-top: 16px; }
    #detailSection h3 { margin: 0 0 4px; font-size: 15px; color: #222; }
    #detailSection h4 { margin: 16px 0 8px; font-size: 13px; color: #333; }
    #detailSection .subtitle { font-size: 12px; color: #888; margin-bottom: 12px; }
    .dl-list { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    @media (max-width: 1100px) {
      .dl-list { grid-template-columns: 1fr; }
    }
    .dl-list li {
      display: flex; align-items: center; gap: 8px;
      padding: 7px 12px; border-radius: 9px; border: 1px solid #e0e0e0;
      background: #f8f9ff;
    }
    .dl-list li .ext {
      background: #1a73e8; color: #fff; border-radius: 5px;
      padding: 2px 7px; font-size: 11px; font-weight: 700; min-width: 54px;
      text-align: center; flex-shrink: 0;
    }
    .dl-list li .fname {
      flex: 1; font-family: monospace; font-size: 12.5px; color: #333;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .dl-list li .actions { display: flex; gap: 6px; flex-shrink: 0; }
    .dl-list li .actions a {
      display: inline-flex; align-items: center; gap: 4px;
      padding: 4px 11px; border-radius: 6px; font-size: 12px; font-weight: 600;
      text-decoration: none; border: 1px solid; transition: background 0.15s, color 0.15s;
      white-space: nowrap;
    }
    .dl-list li .actions .btn-dl {
      background: #1a73e8; color: #fff; border-color: #1a73e8;
    }
    .dl-list li .actions .btn-dl:hover { background: #1558b0; border-color: #1558b0; }
    .dl-list li .actions .btn-open {
      background: #fff; color: #1a73e8; border-color: #1a73e8;
    }
    .dl-list li .actions .btn-open:hover { background: #e8f0fe; }
    .detail-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
      gap: 12px;
    }
    .detail-cell {
      height: 260px; border: 1px solid #e8e8e8; border-radius: 8px; padding: 4px;
    }
    /* Centered wait overlay dimming the page while detail charts build */
    #waitOverlay {
      display: none; position: fixed; inset: 0; z-index: 1000;
      background: rgba(0, 0, 0, 0.45);
      align-items: center; justify-content: center;
    }
    #waitOverlay.open { display: flex; }
    #waitOverlay .box {
      background: #fff; border-radius: 12px; padding: 22px 30px;
      font-size: 15px; font-weight: 600; color: #1a73e8;
      box-shadow: 0 8px 40px rgba(0, 0, 0, 0.25);
    }
    #waitOverlay .box::before {
      content: ""; display: inline-block; width: 16px; height: 16px;
      border: 2px solid #1a73e8; border-top-color: transparent;
      border-radius: 50%; margin-right: 10px; vertical-align: -3px;
      animation: dspin 0.8s linear infinite;
    }
    @keyframes dspin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <h2>Sysbench Throughput Jitter &mdash; per-second TPS distribution</h2>
  <div class="wrap">
    <div class="card">
      <label for="serverSel">Servers (multi-select)</label>
      <select id="serverSel" multiple></select>
      <div class="hint">Tip: Ctrl/Cmd-click to select multiple.</div>

      <div class="row">
        <div>
          <label for="memSel">Buffer pool (multi-select)</label>
          <select id="memSel" multiple></select>
        </div>
        <div>
          <label for="tpSel">Thread pool size (multi-select)</label>
          <select id="tpSel" multiple></select>
        </div>
      </div>

      <label for="osSel">Oversubscribe (multi-select)</label>
      <select id="osSel" multiple></select>
      <div class="hint">"off" runs (thread pool disabled) are unaffected by the oversubscribe selection.</div>

      <label>Display</label>
      <div style="display: flex; gap: 16px;">
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="displayMode" value="graph" checked> Graph
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="displayMode" value="table"> Table
        </label>
      </div>

      <label>Metric</label>
      <div style="display: flex; gap: 16px;">
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="metricMode" value="tps" checked> TPS
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="metricMode" value="lat95"> Latency p95
        </label>
      </div>

      <label>Median display</label>
      <div style="display: flex; gap: 16px;">
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="checkbox" id="linesChk" checked> Lines
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="checkbox" id="barsChk"> Bars
        </label>
      </div>

      <label>Distribution shape</label>
      <div style="display: flex; gap: 16px;">
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="shapeMode" value="box" checked> Box
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="shapeMode" value="violin"> Violin
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="shapeMode" value="circle"> Circle
        </label>
      </div>
      <div class="hint">Violins expose bimodal jitter (normal vs stall seconds) that boxes flatten
        into whiskers. Circle draws a single marker at the median (sample points do not apply).</div>

      <label>Sample points</label>
      <div style="display: flex; gap: 16px;">
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="pointsMode" value="all" checked> All
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="pointsMode" value="outliers"> Outliers
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="pointsMode" value="none"> None
        </label>
      </div>

      <div class="btnrow">
        <button id="resetBtn">Reset</button>
      </div>

      <div class="hint">
        Each box summarises the per-second TPS samples of one configuration at
        one client thread count; the dots are the individual seconds (evenly
        downsampled). Wide boxes and long tails = unstable throughput.
        <b>At most {{MAX_SERIES}} series are drawn</b> &mdash; narrow the
        selection when the warning appears.
        Tip: double-click a legend entry to isolate one series; double-click
        again to bring the others back.
        Click a data point to show its download links and InnoDB metric graphs under the chart.
        Shareable URL parameters:
        <code>?display=graph|table&amp;metric=tps|lat95&amp;shape=box|violin|circle&amp;points=all|outliers|none&amp;median=lines,bars|none&amp;server=...&amp;mem=2,32&amp;tp=off,80&amp;os=2,3&amp;hide=...</code>
        (each list also accepts <code>all</code>; <code>hide</code> lists series
        switched off via legend clicks and updates automatically).
      </div>
    </div>

    <div class="card">
      <div id="warn"></div>
      <div id="chart"></div>
      <div id="tableView" style="display: none;"></div>
      <div id="detailSection"></div>
      <div id="waitOverlay"><div class="box" id="waitMsg"></div></div>
    </div>
  </div>

<script>
{{DATA_BLOCK}}

const MAX_SERIES = {{MAX_SERIES}};

function el(id) { return document.getElementById(id); }

function fillOptions(selectEl, values, formatter=(v)=>v) {
  selectEl.innerHTML = "";
  values.forEach(v => {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = formatter(v);
    selectEl.appendChild(opt);
  });
}

function getSelectedValues(selectEl) {
  return Array.from(selectEl.selectedOptions).map(o => o.value);
}

function setSelected(selectEl, predicate) {
  Array.from(selectEl.options).forEach(opt => { opt.selected = predicate(opt.value); });
}

function numeric(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function tpValue(v) { return v === "off" ? "off" : Number(v); }

function tpLabel(tp, os) { return tp === "off" ? "TP off" : `TP ${tp} os${os}`; }

function traceColor(i, n, alpha) {
  const hue = Math.round((i * 360) / Math.max(n, 1));
  const light = i % 2 ? 54 : 42;
  return alpha === undefined
    ? `hsl(${hue}, 72%, ${light}%)`
    : `hsla(${hue}, 72%, ${light}%, ${alpha})`;
}

const DEFAULT_MEM = MEMS.includes(12) ? 12 : MEMS[0];
const SERVERS = [...new Set(RUNS.map(r => r.server))].sort();

// Series names hidden via legend clicks; persisted in the URL (?hide=...) so
// a link reproduces the exact graph configuration
let HIDDEN = new Set();

function median(values) {
  const v = [...values].sort((a, b) => a - b);
  const mid = v.length >> 1;
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

// Metric selected in the sidebar: per-second TPS or per-second p95 latency
function metricInfo() {
  return radioVal("metricMode") === "lat95"
    ? { field: "lat", title: "Latency p95, ms", unit: "ms",
        label: "p95 latency", lowerBetter: true,
        fmt: v => v.toFixed(2) }
    : { field: "samples", title: "Throughput, trx / sec", unit: "tps",
        label: "TPS", lowerBetter: false,
        fmt: v => Math.round(v).toLocaleString() };
}

function radioVal(name) {
  return document.querySelector(`input[name="${name}"]:checked`).value;
}

function setRadio(name, value) {
  const radio = document.querySelector(`input[name="${name}"][value="${value}"]`);
  if (radio) radio.checked = true;
}

// URL parameters, e.g. ?display=table&shape=violin&points=outliers
//   &median=lines,bars&server=mysql%209.7.2&mem=2,32&tp=off,80&os=2,3
// (each list also accepts "all")
function applyListParam(params, name, selectEl, values, normalize) {
  const raw = params.get(name);
  if (!raw) return;
  if (raw.toLowerCase() === "all") {
    setSelected(selectEl, _ => true);
    return;
  }
  const wanted = new Set(raw.split(",").map(s => normalize(s.trim())));
  if (values.some(v => wanted.has(String(v)))) {
    setSelected(selectEl, v => wanted.has(v));
  }
}

function applyUrlParams() {
  const params = new URLSearchParams(window.location.search);

  const display = params.get("display");
  if (display === "graph" || display === "table") setRadio("displayMode", display);
  const metric = params.get("metric");
  if (metric === "tps" || metric === "lat95") setRadio("metricMode", metric);
  const shape = params.get("shape");
  if (["box", "violin", "circle"].includes(shape)) setRadio("shapeMode", shape);
  const points = params.get("points");
  if (["all", "outliers", "none"].includes(points)) setRadio("pointsMode", points);
  const med = params.get("median");
  if (med !== null) {
    const wanted = new Set(med.split(",").map(s => s.trim()));
    el("linesChk").checked = wanted.has("lines");
    el("barsChk").checked = wanted.has("bars");
  }

  const hide = params.get("hide");
  if (hide) {
    HIDDEN = new Set(hide.split(",").map(s => s.trim()).filter(Boolean));
  }

  applyListParam(params, "server", el("serverSel"), SERVERS, s => s);
  // Accept "4" and "4G" alike for the buffer pool
  applyListParam(params, "mem", el("memSel"), MEMS, s => s.replace(/[Gg]$/, ""));
  applyListParam(params, "tp", el("tpSel"), TP_SIZES, s => s.toLowerCase());
  applyListParam(params, "os", el("osSel"), OS_VALUES, s => s);
}

function syncUrl() {
  const params = new URLSearchParams(window.location.search);
  params.set("display", radioVal("displayMode"));
  params.set("metric", radioVal("metricMode"));
  params.set("shape", radioVal("shapeMode"));
  params.set("points", radioVal("pointsMode"));
  const med = [];
  if (el("linesChk").checked) med.push("lines");
  if (el("barsChk").checked) med.push("bars");
  params.set("median", med.length ? med.join(",") : "none");
  const servers = getSelectedValues(el("serverSel"));
  params.set("server", servers.length === SERVERS.length ? "all" : servers.join(","));
  const mems = getSelectedValues(el("memSel"));
  params.set("mem", mems.length === MEMS.length ? "all" : mems.join(","));
  const tps = getSelectedValues(el("tpSel"));
  params.set("tp", tps.length === TP_SIZES.length ? "all" : tps.join(","));
  const osVals = getSelectedValues(el("osSel"));
  params.set("os", osVals.length === OS_VALUES.length ? "all" : osVals.join(","));
  if (HIDDEN.size) {
    params.set("hide", [...HIDDEN].join(","));
  } else {
    params.delete("hide");
  }
  try {
    history.replaceState(null, "", `${window.location.pathname}?${params}`);
  } catch (e) { /* file:// in some browsers forbids replaceState */ }
}

// Median TPS table: one row per series, one column per thread count.
// Best value per column: green background; worst: its ratio color as
// background; every cell shows its percentage of the column's best.
function buildTable(series, cats) {
  const container = el("tableView");
  container.innerHTML = "";

  const MET = metricInfo();
  const valsOf = p => p[MET.field];

  const caption = document.createElement("div");
  caption.className = "caption";
  caption.textContent = `Median per-second ${MET.label} by client threads ` +
    "(from the downsampled per-second samples)";
  container.appendChild(caption);

  const maps = series.map(s => new Map(s.pts.map(p => [String(p.threads), p])));
  const bestByCol = new Map(), worstByCol = new Map();
  cats.forEach(c => {
    let best = null, worst = null;
    maps.forEach(m => {
      const p = m.get(c);
      if (!p) return;
      const v = median(valsOf(p));
      if (best === null || (MET.lowerBetter ? v < best : v > best)) best = v;
      if (worst === null || (MET.lowerBetter ? v > worst : v < worst)) worst = v;
    });
    bestByCol.set(c, best);
    worstByCol.set(c, worst);
  });

  // ratio of best, 0..1 -> green within 10% of the best, fading through
  // yellow/orange into red at 10% of the best
  function ratioColor(ratio) {
    const t = Math.max(0, Math.min(1, (ratio - 0.1) / 0.8));
    return `hsl(${Math.round(120 * t)}, 65%, 30%)`;
  }

  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const nameTh = document.createElement("th");
  nameTh.className = "name";
  nameTh.textContent = "Server | Buffer pool | Thread pool";
  headRow.appendChild(nameTh);
  cats.forEach(c => {
    const th = document.createElement("th");
    th.textContent = `${c} th`;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  series.forEach((s, i) => {
    const tr = document.createElement("tr");
    const baseBg = i % 2 ? "#eeeeee" : "#ffffff";
    tr.style.background = baseBg;
    tr.addEventListener("mouseenter", () => { tr.style.background = "#c7c7c7"; });
    tr.addEventListener("mouseleave", () => { tr.style.background = baseBg; });
    const nameTd = document.createElement("td");
    nameTd.className = "name";
    nameTd.textContent = s.name;
    tr.appendChild(nameTd);

    cats.forEach(c => {
      const td = document.createElement("td");
      const p = maps[i].get(c);
      if (p) {
        const v = median(valsOf(p));
        const best = bestByCol.get(c);
        const ratio = MET.lowerBetter ? best / v : v / best;
        const isBest = v === best;
        const isWorst = !isBest && v === worstByCol.get(c);
        const pct = document.createElement("span");
        pct.className = "pct";
        pct.textContent = `${Math.round(ratio * 100)}%`;
        td.appendChild(pct);
        td.appendChild(document.createTextNode(MET.fmt(v)));
        if (isBest) {
          td.style.background = "hsl(120, 50%, 38%)";
          td.style.color = "#ffffff";
          td.style.fontWeight = "700";
        } else if (isWorst) {
          td.style.background = ratioColor(ratio);
          td.style.color = "#ffffff";
        } else {
          td.style.color = ratioColor(ratio);
        }
        const vs = valsOf(p);
        const mn = Math.min(...vs), mx = Math.max(...vs);
        const mean = vs.reduce((a, x) => a + x, 0) / vs.length;
        const sd = Math.sqrt(vs.reduce((a, x) => a + (x - mean) * (x - mean), 0)
                             / vs.length);
        td.title = `${Math.round(ratio * 100)}% of the best in this column  ·  ` +
                   `min: ${mn.toLocaleString()}  max: ${mx.toLocaleString()}  ` +
                   `stddev: ${Math.round(sd).toLocaleString()}`;
      } else {
        td.textContent = "—";
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);
}

// Series matching the current selection: one per (server, mem, tp[, os])
function selectedSeries() {
  const servers = getSelectedValues(el("serverSel"));
  const mems = getSelectedValues(el("memSel")).map(numeric).filter(v => v !== null);
  const tps = getSelectedValues(el("tpSel")).map(tpValue);
  const osVals = getSelectedValues(el("osSel")).map(numeric).filter(v => v !== null);

  const series = [];
  (servers.length ? servers : [SERVERS[0]]).forEach(server => {
    (mems.length ? mems : [DEFAULT_MEM]).forEach(mem => {
      (tps.length ? tps : TP_SIZES).forEach(tp => {
        const osList = tp === "off" ? [null] : (osVals.length ? osVals : OS_VALUES);
        osList.forEach(os => {
          const pts = RUNS
            .filter(r => r.server === server && r.mem_gb === mem &&
                         r.tp === tp && r.os === os)
            .sort((a,b)=>a.threads-b.threads);
          if (!pts.length) return;
          series.push({ name: `${server} | ${mem}G | ${tpLabel(tp, os)}`, pts });
        });
      });
    });
  });
  return series;
}

function render() {
  syncUrl();
  const all = selectedSeries();
  const warn = el("warn");
  if (all.length > MAX_SERIES) {
    warn.style.display = "block";
    warn.textContent = `The selection yields ${all.length} series; ` +
      `only the first ${MAX_SERIES} are drawn. ` +
      `Deselect servers / buffer pools / thread pool sizes to narrow it down.`;
  } else {
    warn.style.display = "none";
  }
  const series = all.slice(0, MAX_SERIES);

  // Thread-count categories present in the current selection, in axis order
  const cats = [...new Set(series.flatMap(s => s.pts.map(p => p.threads)))]
    .sort((a, b) => a - b).map(String);
  const catIdx = new Map(cats.map((c, k) => [c, k]));

  const display = document.querySelector('input[name="displayMode"]:checked').value;
  el("chart").style.display = display === "graph" ? "" : "none";
  el("tableView").style.display = display === "graph" ? "none" : "";
  if (display === "table") {
    buildTable(series, cats);
    return;
  }

  // One slot formula drives the boxes (via boxmode group, boxgap 0.1 /
  // boxgroupgap 0.3), the median bars (via explicit offset+width) and the
  // median line vertices, so all three stay combined by construction.
  const N = series.length;
  const slotW = 0.9 / N;                              // 0.9 = 1 - boxgap
  const slotCenter = i => -0.45 + (i + 0.5) * slotW;
  const barW = slotW * 0.7;                           // 0.7 = 1 - boxgroupgap

  const MET = metricInfo();
  const valsOf = p => p[MET.field];

  // Median shown as semi-transparent bars from zero and/or as lines
  // connecting the medians -- each toggled independently in the sidebar
  const barTraces = !el("barsChk").checked ? [] : series.map((s, i) => ({
    type: "bar",
    x: s.pts.map(p => catIdx.get(String(p.threads))),
    y: s.pts.map(p => median(valsOf(p))),
    width: barW,
    offset: slotCenter(i) - barW / 2,
    marker: { color: traceColor(i, series.length, 0.30) },
    legendgroup: s.name,
    showlegend: false,
    hoverinfo: "skip",
    visible: HIDDEN.has(s.name) ? "legendonly" : true,
  }));

  const shape = document.querySelector('input[name="shapeMode"]:checked').value;
  const pointsMode = document.querySelector('input[name="pointsMode"]:checked').value;
  const points = pointsMode === "none" ? false : pointsMode;
  // The denser the chart, the more transparent the jitter cloud; circle
  // markers stay near-opaque since there is only one per column
  const ptAlpha = shape === "circle" ? 0.85
    : N <= 3 ? 0.45 : N <= 6 ? 0.35 : N <= 9 ? 0.25 : 0.2;

  const distTraces = series.map((s, i) => {
    if (shape === "circle") {
      // A single circle at the median of each configuration
      return {
        type: "scatter",
        mode: "markers",
        name: s.name,
        x: s.pts.map(p => catIdx.get(String(p.threads)) + slotCenter(i)),
        y: s.pts.map(p => median(valsOf(p))),
        customdata: s.pts.map(p => {
          const vs = valsOf(p);
          const mean = vs.reduce((a, v) => a + v, 0) / vs.length;
          const sd = Math.sqrt(vs.reduce(
            (a, v) => a + (v - mean) * (v - mean), 0) / vs.length);
          return [Math.min(...vs), Math.max(...vs), sd, p.threads];
        }),
        marker: { size: 10, color: traceColor(i, N, ptAlpha),
                  line: { color: traceColor(i, N), width: 1.5 } },
        legendgroup: s.name,
        visible: HIDDEN.has(s.name) ? "legendonly" : true,
        hovertemplate:
          "<b>%{fullData.name}</b><br>" +
          "Threads: %{customdata[3]}<br>" +
          `Median: %{y:,.1f} ${MET.unit}<br>` +
          "Min: %{customdata[0]:,.1f}<br>" +
          "Max: %{customdata[1]:,.1f}<br>" +
          "Stddev: %{customdata[2]:,.1f}" +
          "<extra></extra>",
      };
    }
    const x = [], y = [], cd = [];
    s.pts.forEach(p => {
      const xi = catIdx.get(String(p.threads));
      // Per-config stats shown in the point tooltip
      const vs = valsOf(p);
      const mn = Math.min(...vs);
      const mx = Math.max(...vs);
      const mean = vs.reduce((a, v) => a + v, 0) / vs.length;
      const sd = Math.sqrt(
        vs.reduce((a, v) => a + (v - mean) * (v - mean), 0) / vs.length);
      vs.forEach(v => {
        x.push(xi);
        y.push(v);
        cd.push([mn, mx, sd, p.threads]);
      });
    });
    const t = {
      name: s.name,
      x: x,
      y: y,
      jitter: 0.6,
      pointpos: 0,
      fillcolor: "rgba(255, 255, 255, 0.85)",
      line: { color: traceColor(i, series.length), width: 1.6 },
      marker: { size: 2.5, color: traceColor(i, series.length, ptAlpha) },
      alignmentgroup: "cfg",
      offsetgroup: String(i),
      legendgroup: s.name,
      visible: HIDDEN.has(s.name) ? "legendonly" : true,
    };
    if (shape === "violin") {
      t.type = "violin";
      t.points = points;
      t.scalemode = "width";
    } else {
      t.type = "box";
      t.boxpoints = points;
      t.whiskerwidth = 0.6;
    }
    if (points) {
      t.customdata = cd;
      t.hoveron = "points";
      t.hovertemplate =
        "<b>%{fullData.name}</b><br>" +
        "Threads: %{customdata[3]}<br>" +
        `Value: %{y:,.1f} ${MET.unit}<br>` +
        "Min: %{customdata[0]:,.1f}<br>" +
        "Max: %{customdata[1]:,.1f}<br>" +
        "Stddev: %{customdata[2]:,.1f}" +
        "<extra></extra>";
    } else {
      // No points: fall back to the native box/violin stats hover
      t.hoveron = shape === "violin" ? "violins" : "boxes";
      t.hoverinfo = "y+name";
    }
    return t;
  });

  // Lines connecting the medians of each series across thread counts. On a
  // category axis scatter accepts numeric x (0 = first category), so each
  // vertex sits at the same slot centre as the bar and box.
  const medianLines = !el("linesChk").checked ? [] : series.map((s, i) => ({
    type: "scatter",
    mode: "lines+markers",
    x: s.pts.map(p => catIdx.get(String(p.threads)) + slotCenter(i)),
    y: s.pts.map(p => median(valsOf(p))),
    line: { color: traceColor(i, series.length, 0.45), width: 4 },
    marker: { size: 4, color: traceColor(i, series.length) },
    legendgroup: s.name,
    showlegend: false,
    hoverinfo: "skip",
    visible: HIDDEN.has(s.name) ? "legendonly" : true,
  }));

  const traces = [...barTraces, ...medianLines, ...distTraces];

  // Vertical dividing lines between the thread-count columns, so it is
  // obvious which boxes belong to which client thread number
  const dividers = cats.slice(0, -1).map((c, i) => ({
    type: "line", xref: "x", yref: "paper", layer: "below",
    x0: i + 0.5, x1: i + 0.5, y0: 0, y1: 1,
    line: { color: "#8a8a8a", width: 1, dash: "dot" },
  }));

  const layout = {
    title: { text: (MET.lowerBetter
        ? "Latency p95 vs Threads (sysbench OLTP read-write)"
        : "Per-second throughput jitter (sysbench OLTP read-write)") +
      "<br><sup>up to {{SAMPLES}} of the ~900 per-second samples shown per configuration (evenly downsampled)</sup>" },
    boxmode: "group",
    violinmode: "group",
    barmode: "group",
    // Bars and boxes use separate gap settings; keep them identical so the
    // median bars line up exactly under their boxes. The outer gap is small
    // so the groups reach close to the dividing lines.
    boxgap: 0.1,
    boxgroupgap: 0.3,
    violingap: 0.1,
    violingroupgap: 0.3,
    bargap: 0.1,
    bargroupgap: 0.3,
    shapes: dividers,
    // Numeric axis with category-index positions: boxes, bars and median
    // lines all share the same coordinate system (on a real category axis
    // the lines' fractional x values would become separate categories)
    xaxis: {
      title: "Client threads",
      tickvals: cats.map((c, i) => i),
      ticktext: cats,
      range: [-0.55, cats.length - 0.45],
      zeroline: false,
    },
    yaxis: { title: MET.title, rangemode: "tozero", nticks: 20 },
    // Vertical legend to the right of the plot
    legend: {
      orientation: "v",
      x: 1.02, y: 1,
      xanchor: "left", yanchor: "top",
      tracegroupgap: 0,
      font: { size: 11 },
    },
    hoverlabel: { font: { size: 15 }, namelength: -1 },
    margin: { l: 70, r: 20, t: 60, b: 60 },
  };
  Plotly.react("chart", traces, layout, { responsive: true })
    .then(attachLegendSync)
    .then(() => attachClick(series, barTraces.length + medianLines.length, cats))
    .then(() => attachHoverOpacity({
      barCount: barTraces.length,
      lineStart: barTraces.length,
      lineCount: medianLines.length,
      distStart: barTraces.length + medianLines.length,
      n: N,
      ptBase: series.map((s, i) => traceColor(i, N, ptAlpha)),
      full: series.map((s, i) => traceColor(i, N)),
      lineBase: series.map((s, i) => traceColor(i, N, 0.45)),
      barBase: series.map((s, i) => traceColor(i, N, 0.30)),
      barHover: series.map((s, i) => traceColor(i, N, 0.55)),
    }));
}

// ---- download modal (per-run log files; all runs are individual here) ----
const BASE_URL = "{{BASE_URL}}";
// mutex_metrics.csv and pt-pmp.txt are deliberately not offered
const LOG_EXTS = ["sysbench.txt", "iostat.txt", "vmstat.txt", "dstat.txt",
                  "mpstat.txt", "innodb.txt", "stat-thpool.txt", "stat-thr.txt"];
const TIER_EXTS = ["cnf.txt", "vars.txt", "status.txt", "pt-mysql-summary.txt"];

function serverToPath(server) {
  // "Percona-Server 9.7.1-1" -> "Percona-Server/9.7.1-1"
  const idx = server.indexOf(" ");
  return server.slice(0, idx) + "/" + server.slice(idx + 1);
}

function makeListItem(ext, fname, url) {
  const li = document.createElement('li');
  li.innerHTML = `
    <span class="ext">.${ext}</span>
    <span class="fname">${fname}</span>
    <span class="actions">
      <a class="btn-dl" href="${url}" download="${fname}" title="Download">&#8595; Download</a>
      <a class="btn-open" href="${url}" target="_blank" rel="noopener" title="Open in new tab">&#8599; Open</a>
    </span>`;
  return li;
}

// InnoDB metric charts drawn under the downloads when a node is clicked.
// rate:true = cumulative counter shown as per-second delta.
const INNODB_CHARTS = [
  {title: "Buffer pool read requests", unit: "/s", rate: true,
   vars: ["buffer_pool_read_requests"]},
  {title: "Buffer pool reads (from disk)", unit: "/s", rate: true,
   vars: ["buffer_pool_reads"]},
  {title: "Buffer pool pages dirty / free", unit: "pages", rate: false,
   vars: ["buffer_pool_pages_dirty", "buffer_pool_pages_free"]},
  {title: "Pages read / written", unit: "/s", rate: true,
   vars: ["buffer_pages_read", "buffer_pages_written"]},
  {title: "OS data reads / writes", unit: "/s", rate: true,
   vars: ["os_data_reads", "os_data_writes"]},
  {title: "Row lock waits", unit: "/s", rate: true, vars: ["lock_row_lock_waits"]},
  {title: "Row lock time", unit: "ms/s", rate: true, vars: ["lock_row_lock_time"]},
  {title: "DML operations", unit: "/s", rate: true,
   vars: ["dml_reads", "dml_inserts", "dml_updates", "dml_deletes"]},
  {title: "Transactions committed", unit: "/s", rate: true,
   vars: ["trx_rw_commits", "trx_commits_insert_update"]},
  {title: "Log writes", unit: "/s", rate: true,
   vars: ["log_writes", "log_write_requests"]},
  {title: "Active transactions", unit: "", rate: false,
   vars: ["trx_active_transactions"]},
];
const DETAIL_COLORS = ['#2196F3', '#4CAF50', '#FF9800', '#9C27B0',
                       '#F44336', '#00BCD4', '#795548', '#607D8B'];

// Thread pool status charts (Percona Server), drawn before the InnoDB ones;
// all values are per-snapshot gauges from SHOW GLOBAL STATUS
const THPOOL_CHARTS = [
  {title: "Thread pool threads", unit: "", rate: false,
   vars: ["Threadpool_threads", "Threadpool_idle_threads"]},
  {title: "Requests waiting in queue", unit: "", rate: false,
   vars: ["Threadpool_requests_waiting_in_queue",
          "Threadpool_requests_waiting_in_hp_queue"]},
  {title: "Requests starved in queue", unit: "", rate: false,
   vars: ["Threadpool_requests_starved_in_queue"]},
  {title: "Average queue wait", unit: "µs", rate: false,
   vars: ["Threadpool_average_queue_wait_us",
          "Threadpool_average_hp_queue_wait_us"]},
];

const innodbCache = {};   // fileBase -> {t: [...], v: {name: [...]}}
const thpoolCache = {};   // fileBase -> {t: [...], v: {name: [...]}}
let detailToken = 0;
let detailPlots = [];

function clearDetail() {
  detailPlots.forEach(div => Plotly.purge(div));
  detailPlots = [];
  el("detailSection").innerHTML = "";
}

function waitShow(text) {
  el("waitMsg").textContent = text;
  el("waitOverlay").classList.add("open");
}

function waitHide() {
  el("waitOverlay").classList.remove("open");
}

// Parse the .innodb.txt CSV: "timestamp,<metric>,..." header, one row per
// second; only the columns used by INNODB_CHARTS are kept
function parseInnodb(text) {
  const lines = text.split("\n").filter(l => l.trim());
  const header = lines[0].split(",");
  const wanted = new Set(INNODB_CHARTS.flatMap(c => c.vars));
  const colIdx = {};
  header.forEach((name, i) => { if (wanted.has(name)) colIdx[name] = i; });
  const t = [], v = {};
  Object.keys(colIdx).forEach(name => { v[name] = []; });
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(",");
    const ts = parseFloat(cells[0]);
    if (!Number.isFinite(ts)) continue;
    t.push(ts);
    for (const name in colIdx) {
      const x = parseFloat(cells[colIdx[name]]);
      v[name].push(Number.isFinite(x) ? x : null);
    }
  }
  return { t: t, v: v };
}

// Parse the .stat-thpool.txt snapshots: "TS<TAB>Variable_name<TAB>Value" lines,
// one block of Threadpool_% variables per second. Composite values like
// "avg: 18131.641, min: ..." contribute their avg component.
function parseThpool(text) {
  const t = [], v = {};
  let cur = null;
  for (const line of text.split("\n")) {
    const parts = line.split("\t");
    if (parts.length < 3) continue;
    const ts = parseFloat(parts[0]);
    if (!Number.isFinite(ts)) continue;
    const m = parts[2].match(/avg:\s*([0-9.]+)/);
    const val = parseFloat(m ? m[1] : parts[2]);
    if (ts !== cur) { cur = ts; t.push(ts); }
    (v[parts[1]] = v[parts[1]] || []).push(Number.isFinite(val) ? val : null);
  }
  return { t: t, v: v };
}

// Cumulative counter -> per-second rate (null on gaps and counter resets)
function rateSeries(t, vals) {
  const out = [null];
  for (let i = 1; i < vals.length; i++) {
    const dt = t[i] - t[i - 1];
    if (vals[i] === null || vals[i - 1] === null || dt <= 0 ||
        vals[i] < vals[i - 1]) {
      out.push(null);
    } else {
      out.push((vals[i] - vals[i - 1]) / dt);
    }
  }
  return out;
}

async function showDetail(p) {
  clearDetail();
  const token = ++detailToken;
  const section = el("detailSection");
  const path = serverToPath(p.server);
  const tpToken = p.tp === "off" ? "tpoff" : `tp${p.tp}_os${p.os}`;

  const h = document.createElement("h3");
  h.textContent = `${p.server} — ${p.mem_gb}G | ${tpLabel(p.tp, p.os)} | ${p.threads} threads`;
  section.appendChild(h);
  const sub = document.createElement("div");
  sub.className = "subtitle";
  sub.textContent =
    `Rows: ${p.rows}  ·  Median TPS: ` +
    `${Math.round(median(p.samples)).toLocaleString()}  ·  Median p95: ` +
    `${median(p.lat).toFixed(2)} ms`;
  section.appendChild(sub);

  const dlHead = document.createElement("h4");
  dlHead.textContent = "Log files";
  section.appendChild(dlHead);
  const list = document.createElement("ul");
  list.className = "dl-list";
  section.appendChild(list);

  // Per-run files for every run of this configuration
  p.runs.forEach(r => {
    const fileBase = `run${r}_${p.rows}_Tier${p.mem_gb}G_${tpToken}_RW_${p.threads}th`;
    LOG_EXTS.forEach(ext => {
      const fname = `${fileBase}.${ext}`;
      list.appendChild(makeListItem(ext, fname, `${BASE_URL}/${path}/${fname}`));
    });
  });
  // Per-tier files
  TIER_EXTS.forEach(ext => {
    const fname = ext === "pt-mysql-summary.txt"
      ? `Tier${p.mem_gb}G_${tpToken}-${ext}`
      : `Tier${p.mem_gb}G_${tpToken}.${ext}`;
    list.appendChild(makeListItem(ext, fname, `${BASE_URL}/${path}/${fname}`));
  });
  // The error log is shared by all thread pool configurations of a tier
  list.appendChild(makeListItem("errlog.txt", `Tier${p.mem_gb}G.errlog.txt`,
                                `${BASE_URL}/${path}/Tier${p.mem_gb}G.errlog.txt`));

  const runNo = p.runs[0];
  const fileBase = `run${runNo}_${p.rows}_Tier${p.mem_gb}G_${tpToken}_RW_${p.threads}th`;
  let scrollTarget = null;

  // Centered overlay dims the page while the charts are prepared; stale-token
  // returns below never hide it, since a newer click owns the overlay then
  waitShow("Please wait, preparing graphs ...");
  section.scrollIntoView({ behavior: "smooth", block: "start" });
  // Let the browser paint the overlay before the heavy work starts
  await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
  if (token !== detailToken) return;

  function loadError(fname, err) {
    const status = document.createElement("p");
    status.className = "subtitle";
    status.textContent = `Could not load ${fname} (${err.message}). ` +
      `If the report was opened as a local file, serve it over HTTP instead, ` +
      `e.g. "python3 -m http.server" in the report directory.`;
    section.appendChild(status);
  }

  // Thread pool status charts (Percona Server) come before the InnoDB ones
  if (p.server.startsWith("Percona")) {
    const tpHead = document.createElement("h4");
    tpHead.textContent = `Thread pool status over time (run ${runNo})`;
    section.appendChild(tpHead);
    scrollTarget = tpHead;

    const tpFile = `${fileBase}.stat-thpool.txt`;
    let tpSample = thpoolCache[fileBase];
    if (tpSample === undefined) {
      waitShow(`Please wait, loading ${tpFile} ...`);
      try {
        const resp = await fetch(`${BASE_URL}/${path}/${tpFile}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        tpSample = thpoolCache[fileBase] = parseThpool(await resp.text());
      } catch (err) {
        // another click may have replaced the section while we waited
        if (token !== detailToken) return;
        loadError(tpFile, err);
        waitHide();
        return;
      }
      if (token !== detailToken) return;
    }
    if (tpSample.t.length) {
      waitShow("Please wait, building thread pool graphs ...");
      if (!await buildChartGrid(section, THPOOL_CHARTS, tpSample, token)) return;
    } else {
      const note = document.createElement("p");
      note.className = "subtitle";
      note.textContent = "No thread pool status snapshots for this run " +
        "(thread pool disabled?).";
      section.appendChild(note);
    }
  }

  // InnoDB charts from the first run's .innodb.txt
  const idbHead = document.createElement("h4");
  idbHead.textContent = `InnoDB metrics over time (run ${runNo})`;
  section.appendChild(idbHead);
  if (!scrollTarget) scrollTarget = idbHead;

  if (!(fileBase in innodbCache)) {
    waitShow(`Please wait, loading ${fileBase}.innodb.txt ...`);
    try {
      const resp = await fetch(`${BASE_URL}/${path}/${fileBase}.innodb.txt`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      innodbCache[fileBase] = parseInnodb(await resp.text());
    } catch (err) {
      // another click may have replaced the section while we waited
      if (token !== detailToken) return;
      loadError(`${fileBase}.innodb.txt`, err);
      waitHide();
      return;
    }
    if (token !== detailToken) return;
  }
  waitShow("Please wait, building InnoDB graphs ...");
  if (!await buildChartGrid(section, INNODB_CHARTS, innodbCache[fileBase], token)) return;

  waitHide();
  // Bring the freshly built graphs to the top of the screen
  scrollTarget.scrollIntoView({ behavior: "smooth", block: "start" });
}

// One grid of small line charts; built one chart per tick so the wait
// message stays painted and the page remains responsive. Returns false
// when a newer click aborted the build.
async function buildChartGrid(section, specs, sample, token) {
  const t0 = sample.t[0];
  const minutes = sample.t.map(ts => (ts - t0) / 60);
  const grid = document.createElement("div");
  grid.className = "detail-grid";
  section.appendChild(grid);

  for (const spec of specs) {
    if (token !== detailToken) return false;
    const vars = spec.vars.filter(name => sample.v[name]);
    if (!vars.length) continue;
    const cell = document.createElement("div");
    cell.className = "detail-cell";
    grid.appendChild(cell);

    const traces = vars.map((name, i) => ({
      type: "scatter",
      mode: "lines",
      name: name,
      x: minutes,
      y: spec.rate ? rateSeries(sample.t, sample.v[name]) : sample.v[name],
      line: { width: 1.5, color: DETAIL_COLORS[i % DETAIL_COLORS.length] },
      connectgaps: false,
      hovertemplate: `${name}: %{y:,.1f} ${spec.unit}<extra></extra>`,
    }));
    Plotly.newPlot(cell, traces, {
      title: { text: spec.title + (spec.unit ? ` (${spec.unit})` : ""),
               font: { size: 12 } },
      margin: { l: 55, r: 10, t: 34, b: 34 },
      showlegend: vars.length > 1,
      legend: { orientation: "h", y: -0.25, font: { size: 9 } },
      xaxis: { title: { text: "minutes", font: { size: 10 } },
               tickfont: { size: 9 } },
      yaxis: { rangemode: "tozero", tickfont: { size: 9 } },
      hovermode: "x unified",
    }, { displayModeBar: false, responsive: true });
    detailPlots.push(cell);
    await new Promise(r => setTimeout(r, 0));
  }
  return token === detailToken;
}

// Click on a data point (or box/violin) opens the download modal
function attachClick(series, distStart, cats) {
  const gd = el("chart");
  gd.removeAllListeners('plotly_click');
  gd.on('plotly_click', function(ev) {
    if (!ev.points || !ev.points.length) return;
    const c = ev.points[0].curveNumber;
    if (c < distStart || c >= distStart + series.length) return;
    const s = series[c - distStart];
    // Points carry the thread count in customdata; a click on the box body
    // only gives the numeric category position
    const cd = ev.points[0].customdata;
    const threads = cd ? cd[3] : Number(cats[Math.round(ev.points[0].x)]);
    const p = s.pts.find(pt => pt.threads === threads);
    if (p) showDetail(p);
  });
}

// Legend clicks (and double-click isolate) change trace visibility and fire
// plotly_restyle; mirror the hidden set into the URL. The hover-opacity
// restyles also fire this event, so only sync when the set actually changed.
function attachLegendSync() {
  const gd = el("chart");
  gd.removeAllListeners('plotly_restyle');
  gd.on('plotly_restyle', function() {
    HIDDEN = new Set((gd.data || [])
      .filter(t => t.name && t.showlegend !== false && t.visible === 'legendonly')
      .map(t => t.name));
    const key = [...HIDDEN].sort().join(",");
    if (key !== gd._hiddenKey) {
      gd._hiddenKey = key;
      syncUrl();
    }
  });
}

// While hovering a point, its series' whole jitter cloud and its median
// connecting line turn fully opaque, and its median bar (when enabled)
// becomes less transparent. Hover/unhover only arm a timer; the single
// restyle runs after the pointer settles, so sweeping across dense regions
// stays cheap.
function attachHoverOpacity(cfg) {
  const gd = el("chart");
  const HOVER_MS = 120;
  let timer = null, pending = -1, applied = -1;
  function apply() {
    timer = null;
    if (pending === applied) return;
    applied = pending;
    // One restyle covering bars, median lines and box/violin traces:
    // per-trace value arrays; entries that should not change get their
    // current color re-applied.
    const barIdx = Array.from({ length: cfg.barCount }, (_, k) => k);
    const lineIdx = Array.from({ length: cfg.lineCount }, (_, k) => cfg.lineStart + k);
    const distIdx = Array.from({ length: cfg.n }, (_, k) => cfg.distStart + k);
    const markerColors = [
      ...barIdx.map((_, i) => i === pending ? cfg.barHover[i] : cfg.barBase[i]),
      ...lineIdx.map((_, i) => cfg.full[i]),                 // line vertices stay solid
      ...distIdx.map((_, i) => i === pending ? cfg.full[i] : cfg.ptBase[i]),
    ];
    const lineColors = [
      ...barIdx.map((_, i) => cfg.full[i]),                  // no-op for bar traces
      ...lineIdx.map((_, i) => i === pending ? cfg.full[i] : cfg.lineBase[i]),
      ...distIdx.map((_, i) => cfg.full[i]),                 // box outlines stay solid
    ];
    Plotly.restyle(gd, { "marker.color": markerColors, "line.color": lineColors },
                   [...barIdx, ...lineIdx, ...distIdx]);
  }
  function schedule(i) {
    pending = i;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(apply, HOVER_MS);
  }
  gd.removeAllListeners('plotly_hover');
  gd.on('plotly_hover', function(ev) {
    if (!ev.points || !ev.points.length) return;
    const c = ev.points[0].curveNumber;
    if (c >= cfg.distStart && c < cfg.distStart + cfg.n) schedule(c - cfg.distStart);
  });
  gd.removeAllListeners('plotly_unhover');
  gd.on('plotly_unhover', function() { schedule(-1); });
}

function init() {
  fillOptions(el("serverSel"), SERVERS);
  fillOptions(el("memSel"), MEMS, v => `${v}G`);
  fillOptions(el("tpSel"), TP_SIZES, v => v === "off" ? "off (no thread pool)" : v);
  fillOptions(el("osSel"), OS_VALUES);

  // Default: one series per server (thread pool off, default buffer pool)
  setSelected(el("serverSel"), _ => true);
  setSelected(el("memSel"), v => Number(v) === DEFAULT_MEM);
  setSelected(el("tpSel"), v => v === "off");
  setSelected(el("osSel"), _ => true);

  ["serverSel","memSel","tpSel","osSel","linesChk","barsChk"].forEach(id => {
    el(id).addEventListener("change", render);
  });
  document.querySelectorAll(
    'input[name="shapeMode"], input[name="pointsMode"], input[name="displayMode"], input[name="metricMode"]')
    .forEach(radio => radio.addEventListener("change", render));
  el("resetBtn").addEventListener("click", () => {
    setSelected(el("serverSel"), _ => true);
    setSelected(el("memSel"), v => Number(v) === DEFAULT_MEM);
    setSelected(el("tpSel"), v => v === "off");
    setSelected(el("osSel"), _ => true);
    render();
  });

  applyUrlParams();
  render();
}

window.addEventListener('load', function() { loadPlotly(init); });
</script>
</body>
</html>
"""

MAX_SERIES = 12


def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive throughput-jitter HTML report from benchmark_logs."
    )
    parser.add_argument("--base-dir", default="benchmark_logs",
                        help="Directory with <server>/<version>/ benchmark logs (default: benchmark_logs)")
    parser.add_argument("--output", default="benchmark_report.html",
                        help="Output HTML file (default: benchmark_report.html)")
    parser.add_argument("--max-threads", type=int, default=2560,
                        help="Ignore runs with more client threads than this (default: 2560)")
    parser.add_argument("--samples", type=int, default=200,
                        help="Per-second samples kept per configuration, evenly spaced (default: 200)")
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    if not base_dir.is_dir():
        sys.exit(f"base_dir not found: {base_dir}")

    print(f"Scanning: {base_dir}")
    records = scan_runs(base_dir, args.max_threads, args.samples)
    if not records:
        sys.exit(f"No valid sysbench data found under '{base_dir}'")

    data_block, servers, mems, threads, tps, osv = build_data_block(records)
    out = (TEMPLATE
           .replace("{{DATA_BLOCK}}", data_block)
           .replace("{{MAX_SERIES}}", str(MAX_SERIES))
           .replace("{{SAMPLES}}", str(args.samples))
           .replace("{{BASE_URL}}", str(base_dir)))

    output_file = Path(args.output)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(out)

    print(f"Done. Report written to: {output_file}")
    print(f"  Servers      : {', '.join(servers)}")
    print(f"  Buffer pools : {', '.join(str(m) for m in mems)}")
    print(f"  TP sizes     : {', '.join(str(t) for t in tps)}")
    print(f"  Threads      : {', '.join(str(t) for t in threads)}")
    print(f"  Configs      : {len(records)} (up to {args.samples} samples each)")


if __name__ == "__main__":
    main()
