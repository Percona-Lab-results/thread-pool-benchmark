#!/usr/bin/env python3
"""
Scans benchmark_logs/<server>/<version>/ for
run<R>_<ROWS>_Tier<M>G_(tpoff|tp<S>_os<O>)_RW_<T>th.sysbench.txt files,
parses TPS/QPS, and generates a self-contained interactive Plotly HTML report.
The HTML template is embedded in this script; no external files needed.

Files with "tpoff" in the name are runs with the thread pool disabled
(thread_handling = one-thread-per-connection). The report provides
multi-select controls for buffer pool size (Tier), thread pool size and
oversubscribe.

The report embeds all individual run results; a switch on the page toggles
between averaged runs (default) and individual runs.

Usage:
    python3 generate_report.py [--base-dir=benchmark_logs] [--output=<file>]
                               [--test-type="OLTP Read-Write"]
"""

import argparse
import json
import re
import sys
from collections import Counter
from html import escape
from pathlib import Path

FILENAME_RE = re.compile(
    r"^run(?P<run>\d+)_(?P<rows>\d+[KkMm]?)_Tier(?P<mem>\d+)G_"
    r"(?:tpoff|tp(?P<tp>\d+)_os(?P<os>\d+))_RW_(?P<threads>\d+)th\.sysbench\.txt$"
)
TPS_RE = re.compile(r"transactions:\s*\d+\s*\(([0-9.]+)\s*per sec\.\)")
QPS_RE = re.compile(r"queries:\s*\d+\s*\(([0-9.]+)\s*per sec\.\)")
TOTAL_TIME_RE = re.compile(r"total time:\s*([0-9.]+)s")


# --------------------------------------------------------------------------
# Data collection
# --------------------------------------------------------------------------

def extract_rates(path: Path):
    text = path.read_text(errors="replace")
    tps_match = TPS_RE.search(text)
    qps_match = QPS_RE.search(text)
    if not tps_match or not qps_match:
        return None
    time_match = TOTAL_TIME_RE.search(text)
    duration = float(time_match.group(1)) if time_match else None
    return float(tps_match.group(1)), float(qps_match.group(1)), duration


def iter_sysbench_files(base_dir: Path):
    for server_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        for version_dir in sorted(p for p in server_dir.iterdir() if p.is_dir()):
            for f in sorted(version_dir.glob("run*.sysbench.txt")):
                m = FILENAME_RE.match(f.name)
                if not m:
                    continue
                parsed = extract_rates(f)
                if parsed is None:
                    print(f"  NA result (skipped): {f}", file=sys.stderr)
                    continue
                yield server_dir.name, version_dir.name, m, parsed


def scan_runs(base_dir: Path):
    """One entry per individual run; averaging happens client-side in the report."""
    rows = []
    durations = []
    for server, version, m, (tps, qps, duration) in iter_sysbench_files(base_dir):
        if duration:
            durations.append(duration)
        rows.append({
            "server": f"{server} {version}",
            "run": int(m.group("run")),
            "rows": m.group("rows"),
            "file": m.string[: -len(".sysbench.txt")],
            "mem_gb": int(m.group("mem")),
            # tp: "off" when the thread pool is disabled, else thread_pool_size
            "tp": "off" if m.group("tp") is None else int(m.group("tp")),
            "os": None if m.group("os") is None else int(m.group("os")),
            "threads": int(m.group("threads")),
            "tps": round(tps, 2),
            "qps": round(qps, 2),
        })
    rows.sort(key=lambda r: (r["server"], r["run"], r["mem_gb"],
                             str(r["tp"]), r["os"] or 0, r["threads"]))
    return rows, durations


# --------------------------------------------------------------------------
# pt-summary system info
# --------------------------------------------------------------------------

PT_SUMMARY_KEYS = [
    "Platform", "Release", "Kernel", "Architecture", "Processors", "Models",
]


def parse_pt_summary(base_dir: Path):
    """Extract system properties from pt-summary output, if available."""
    info = {}
    for name in ("pt-summary-full.txt", "pt-summary-brief.txt"):
        path = base_dir / name
        if not path.is_file():
            continue
        section = ""
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("#"):
                section = line.strip("# ").strip()
                continue
            if "|" not in line:
                continue
            key, _, value = line.partition("|")
            key, value = key.strip(), value.strip()
            if key in PT_SUMMARY_KEYS and key not in info:
                info[key] = value
            elif key == "Total" and section.startswith("Memory") and "Memory Total" not in info:
                info["Memory Total"] = value
        if info:
            break
    return info


# --------------------------------------------------------------------------
# HTML generation
# --------------------------------------------------------------------------

def table_rows(pairs):
    """Render zebra-striped <tr> rows for a two-column table."""
    out = []
    for i, (key, value) in enumerate(pairs):
        bg = ' style="background:#fafafa;"' if i % 2 else ""
        out.append(
            f'<tr{bg}>'
            f'<td style="border: 1px solid #ccc; padding: 4px 12px; color: #666;">{escape(str(key))}</td>'
            f'<td style="border: 1px solid #ccc; padding: 4px 12px;">{escape(str(value))}</td>'
            f'</tr>'
        )
    return "\n              ".join(out)


def tp_sort_key(tp):
    """Order thread pool sizes with 'off' first, then numerically."""
    return (0, 0) if tp == "off" else (1, tp)


def build_data_block(rows):
    servers_sorted = sorted({r["server"] for r in rows})
    mems_sorted = sorted({r["mem_gb"] for r in rows})
    threads_sorted = sorted({r["threads"] for r in rows})
    tps_sorted = sorted({r["tp"] for r in rows}, key=tp_sort_key)
    os_sorted = sorted({r["os"] for r in rows if r["os"] is not None})

    block = (
        f"const RUNS = {json.dumps(rows)};\n"
        f"const MEMS = {json.dumps(mems_sorted)};\n"
        f"const THREADS = {json.dumps(threads_sorted)};\n"
        f"const TP_SIZES = {json.dumps(tps_sorted)};\n"
        f"const OS_VALUES = {json.dumps(os_sorted)};"
    )
    return block, servers_sorted, mems_sorted, threads_sorted, tps_sorted, os_sorted


TEMPLATE = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Sysbench Thread Pool Comparison</title>
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
    #chart { height: 620px; cursor: crosshair; }
    #tableView { max-height: 620px; overflow: auto; }
    #tableView table { border-collapse: collapse; width: 100%; font-family: monospace; font-size: 13px; }
    #tableView th, #tableView td { border: 1px solid #ccc; padding: 5px 10px; text-align: right; white-space: nowrap; }
    #tableView th { background: #e8eaf0; color: #555; position: sticky; top: 0; }
    #tableView th.name, #tableView td.name { text-align: left; }
    #tableView td.pt { cursor: pointer; }
    #tableView td.pt:hover { background: #e8f0fe; }
    #tableView .caption { font-family: system-ui, sans-serif; font-size: 13px; font-weight: 700; color: #333; margin: 4px 0 8px; }

    /* Download modal */
    #dlOverlay {
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.45); z-index: 1000;
      align-items: center; justify-content: center;
    }
    #dlOverlay.open { display: flex; }
    #dlModal {
      background: #fff; border-radius: 14px; padding: 24px 28px;
      max-width: 1100px; width: 94%; box-shadow: 0 8px 40px rgba(0,0,0,0.22);
      position: relative; max-height: 88vh; overflow-y: auto;
    }
    #dlModal h3 { margin: 0 0 4px; font-size: 15px; color: #222; }
    #dlModal .subtitle { font-size: 12px; color: #888; margin-bottom: 16px; }
    #dlModal .dl-list { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    @media (max-width: 760px) {
      #dlModal .dl-list { grid-template-columns: 1fr; }
    }
    #dlModal .dl-list li {
      display: flex; align-items: center; gap: 8px;
      padding: 9px 12px; border-radius: 9px; border: 1px solid #e0e0e0;
      background: #f8f9ff;
    }
    #dlModal .dl-list li .ext {
      background: #1a73e8; color: #fff; border-radius: 5px;
      padding: 2px 7px; font-size: 11px; font-weight: 700; min-width: 54px;
      text-align: center; flex-shrink: 0;
    }
    #dlModal .dl-list li .fname {
      flex: 1; font-family: monospace; font-size: 12.5px; color: #333;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    #dlModal .dl-list li .actions { display: flex; gap: 6px; flex-shrink: 0; }
    #dlModal .dl-list li .actions a {
      display: inline-flex; align-items: center; gap: 4px;
      padding: 5px 11px; border-radius: 6px; font-size: 12px; font-weight: 600;
      text-decoration: none; border: 1px solid; transition: background 0.15s, color 0.15s;
      white-space: nowrap;
    }
    #dlModal .dl-list li .actions .btn-dl {
      background: #1a73e8; color: #fff; border-color: #1a73e8;
    }
    #dlModal .dl-list li .actions .btn-dl:hover { background: #1558b0; border-color: #1558b0; }
    #dlModal .dl-list li .actions .btn-open {
      background: #fff; color: #1a73e8; border-color: #1a73e8;
    }
    #dlModal .dl-list li .actions .btn-open:hover { background: #e8f0fe; }
    #dlClose {
      position: absolute; top: 12px; right: 14px; background: none;
      border: none; font-size: 22px; cursor: pointer; color: #999; line-height: 1;
      padding: 2px 6px; border-radius: 6px;
    }
    #dlClose:hover { background: #f0f0f0; color: #333; }
  </style>
</head>
<body>
  <h2>Sysbench Thread Pool Performance &mdash; Interactive Comparison</h2>
  <div class="wrap">
    <div class="card">
      <label for="serverSel">Servers (multi-select)</label>
      <select id="serverSel" multiple></select>
      <div class="hint">Tip: Ctrl/Cmd-click to select multiple. Use "Select all".</div>

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

      <label>Runs</label>
      <div style="display: flex; gap: 16px;">
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="viewMode" value="average" checked> Averaged
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="viewMode" value="individual"> Individual
        </label>
      </div>

      <label>Display</label>
      <div style="display: flex; gap: 16px;">
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="displayMode" value="graph" checked> Graph
        </label>
        <label style="font-weight: 400; display: flex; align-items: center; gap: 6px; margin: 0;">
          <input type="radio" name="displayMode" value="table"> Table
        </label>
      </div>

      <div class="btnrow">
        <button id="allServersBtn">All servers</button>
        <button id="allMemsBtn">All buffer pools</button>
        <button id="allTpsBtn">All TP sizes</button>
        <button id="allOsBtn">All oversubscribe</button>
        <button id="resetBtn">Reset</button>
      </div>

      <div class="hint">
        Chart overlays selected servers at the selected buffer pool / thread pool
        combinations. Missing points are omitted automatically.
        Click a data point to download its log files.
        Shareable URL parameters:
        <code>?display=graph|table&amp;mem=2,32&amp;tp=off,80&amp;os=2,3</code>
        (or <code>mem=all</code>, <code>tp=all</code>, <code>os=all</code>).
      </div>
    </div>

    <div class="card">
      <div id="chart"></div>
      <div id="tableView" style="display: none;"></div>
      <div style="margin-top: 14px; display: grid; grid-template-columns: 1fr 1fr; gap: 14px;">
        <div>
          <div style="font-family: system-ui, sans-serif; font-size: 13px; font-weight: 700; margin-bottom: 6px; color: #333;">
            Percona pt-summary System Info
            <a href="{{BASE_URL}}/pt-summary-full.txt" target="_blank" rel="noopener" style="margin-left: 10px; font-size: 12px; font-weight: 600; text-decoration: none; background: #1a73e8; color: #fff; padding: 3px 10px; border-radius: 5px;">&#8599; Open full pt-summary</a>
          </div>
          <table style="border-collapse: collapse; width: 100%; font-family: monospace; font-size: 13px;">
            <thead>
              <tr style="background: #e8eaf0;">
                <th style="border: 1px solid #ccc; padding: 5px 12px; text-align: left; color: #555;">Property</th>
                <th style="border: 1px solid #ccc; padding: 5px 12px; text-align: left; color: #555;">Value</th>
              </tr>
            </thead>
            <tbody>
              {{SYSTEM_INFO_ROWS}}
            </tbody>
          </table>
        </div>
        <div>
          <div style="font-family: system-ui, sans-serif; font-size: 13px; font-weight: 700; margin-bottom: 6px; color: #333;">Sysbench Run Configuration</div>
          <table style="border-collapse: collapse; width: 100%; font-family: monospace; font-size: 13px;">
            <thead>
              <tr style="background: #e8eaf0;">
                <th style="border: 1px solid #ccc; padding: 5px 12px; text-align: left; color: #555;">Parameter</th>
                <th style="border: 1px solid #ccc; padding: 5px 12px; text-align: left; color: #555;">Value</th>
              </tr>
            </thead>
            <tbody>
              {{RUN_CONFIG_ROWS}}
            </tbody>
          </table>
        </div>
      </div>
      <div style="margin-top: 24px;">
        <div style="font-family: system-ui, sans-serif; font-size: 13px; font-weight: 700; margin-bottom: 6px; color: #333;">About This Graph</div>
        <table style="border-collapse: collapse; width: 100%; font-family: system-ui, sans-serif; font-size: 13px;">
          <thead>
            <tr style="background: #e8eaf0;">
              <th style="border: 1px solid #ccc; padding: 5px 12px; text-align: left; color: #555; width: 30%;">Dimension</th>
              <th style="border: 1px solid #ccc; padding: 5px 12px; text-align: left; color: #555;">Details</th>
            </tr>
          </thead>
          <tbody>
            {{ABOUT_ROWS}}
          </tbody>
        </table>
      </div>
    </div>
  </div>

<script>
{{DATA_BLOCK}}

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
  Array.from(selectEl.options).forEach(opt => {
    opt.selected = predicate(opt.value);
  });
}

function numeric(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// Select option values are strings; thread pool sizes mix "off" and numbers.
function tpValue(v) {
  return v === "off" ? "off" : Number(v);
}

function tpLabel(tp, os) {
  return tp === "off" ? "TP off" : `TP ${tp} os${os}`;
}

const DEFAULT_MEM = MEMS.includes(12) ? 12 : MEMS[0];

let VIEW_MODE = "average";
let DISPLAY_MODE = "graph";

function computeData() {
  if (VIEW_MODE === "individual") {
    return RUNS.map(r => ({
      server: `run${r.run}-${r.server.replace(/ /g, "-")}`,
      rows: r.rows, mem_gb: r.mem_gb, tp: r.tp, os: r.os,
      threads: r.threads, tps: r.tps, qps: r.qps,
      file: r.file,
    }));
  }
  // Average all runs per (server, mem, tp, os, threads)
  const groups = new Map();
  RUNS.forEach(r => {
    const key = `${r.server}|${r.mem_gb}|${r.tp}|${r.os}|${r.threads}`;
    let g = groups.get(key);
    if (!g) {
      g = {server: r.server, rows: r.rows, mem_gb: r.mem_gb, tp: r.tp, os: r.os,
           threads: r.threads, tps: [], qps: []};
      groups.set(key, g);
    }
    g.tps.push(r.tps);
    g.qps.push(r.qps);
  });
  const mean = a => a.reduce((s, v) => s + v, 0) / a.length;
  return Array.from(groups.values()).map(g => ({
    server: g.server, rows: g.rows, mem_gb: g.mem_gb, tp: g.tp, os: g.os,
    threads: g.threads,
    tps: Math.round(mean(g.tps) * 100) / 100,
    qps: Math.round(mean(g.qps) * 100) / 100,
  }));
}

let DATA = computeData();

function serverList() {
  return [...new Set(DATA.map(r => r.server))].sort();
}

// Series matching the current selection: one per (server, mem, tp[, os]) with
// data. Shared by the graph and the table views.
function selectedSeries() {
  const selectedServers = getSelectedValues(el("serverSel"));
  const selectedMems = getSelectedValues(el("memSel")).map(numeric).filter(v => v !== null);
  const selectedTps = getSelectedValues(el("tpSel")).map(tpValue);
  const selectedOs = getSelectedValues(el("osSel")).map(numeric).filter(v => v !== null);

  const servers = selectedServers.length ? selectedServers : [serverList()[0]];
  const mems = selectedMems.length ? selectedMems : [DEFAULT_MEM];
  const tps = selectedTps.length ? selectedTps : TP_SIZES;
  const osVals = selectedOs.length ? selectedOs : OS_VALUES;

  const series = [];
  servers.forEach(server => {
    mems.forEach(mem => {
      tps.forEach(tp => {
        // "off" carries no oversubscribe dimension
        const osList = tp === "off" ? [null] : osVals;
        osList.forEach(os => {
          const pts = DATA
            .filter(r => r.server === server && r.mem_gb === mem &&
                         r.tp === tp && r.os === os && r.tps !== null)
            .sort((a,b)=>a.threads-b.threads);

          if (!pts.length) return;
          series.push({ name: `${server} | ${mem}G | ${tpLabel(tp, os)}`, pts });
        });
      });
    });
  });
  return series;
}

function buildTraces() {
  const metric = "tps";

  return selectedSeries().map(s => ({
    type: "scatter",
    mode: "lines+markers",
    name: s.name,
    x: s.pts.map(p=>p.threads),
    y: s.pts.map(p=>p[metric]),
    customdata: s.pts.map(p=>({server: p.server, rows: p.rows, mem_gb: p.mem_gb,
                               tp: p.tp, os: p.os, tpLabel: tpLabel(p.tp, p.os),
                               threads: p.threads, tps: p.tps, qps: p.qps, file: p.file})),
    marker: { size: 10 },
    hovertemplate:
      '<b>%{customdata.server}</b><br>' +
      'Buffer pool: %{customdata.mem_gb}G<br>' +
      'Thread pool: %{customdata.tpLabel}<br>' +
      'Threads: %{customdata.threads}<br>' +
      'TPS: %{customdata.tps:,.0f}<br>' +
      'QPS: %{customdata.qps:,.0f}' +
      '<extra></extra>',
  }));
}

// TPS table: one row per (server, mem, tp[, os]) series, one column per thread
// count. Cells open the same download modal as clicking a graph point.
function buildTable() {
  const container = el("tableView");
  container.innerHTML = "";

  const caption = document.createElement("div");
  caption.className = "caption";
  caption.textContent = "Transactions per second (TPS) by client threads";
  container.appendChild(caption);

  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const nameTh = document.createElement("th");
  nameTh.className = "name";
  nameTh.textContent = "Server | Buffer pool | Thread pool";
  headRow.appendChild(nameTh);
  THREADS.forEach(t => {
    const th = document.createElement("th");
    th.textContent = `${t} th`;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  selectedSeries().forEach(s => {
    const tr = document.createElement("tr");
    const nameTd = document.createElement("td");
    nameTd.className = "name";
    nameTd.textContent = s.name;
    tr.appendChild(nameTd);

    const byThreads = new Map(s.pts.map(p => [p.threads, p]));
    THREADS.forEach(t => {
      const td = document.createElement("td");
      const p = byThreads.get(t);
      if (p) {
        td.className = "pt";
        td.textContent = Math.round(p.tps).toLocaleString();
        td.title = `TPS: ${p.tps.toLocaleString()}  QPS: ${p.qps.toLocaleString()} (click for log files)`;
        td.addEventListener("click", () => showDownloadModal(p));
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

function layoutForMode() {
  const yTitle = "Transactions per second (TPS)";

  return {
    title: { text: `Sysbench {{TEST_TYPE}}: ${yTitle} vs Threads` },
    xaxis: {
      title: "Threads",
      type: "log",
      tickvals: THREADS,
      ticktext: THREADS.map(String),
    },
    yaxis: { title: yTitle, rangemode: 'tozero' },
    // Vertical legend to the right of the plot so it never obstructs the lines
    legend: {
      orientation: "v",
      x: 1.02, y: 1,
      xanchor: "left", yanchor: "top",
    },
    margin: { l: 70, r: 20, t: 60, b: 60 },
    hovermode: "closest",
    hoverlabel: {
      font: { size: 16 },
      namelength: -1,
    },
  };
}

const BASE_URL = "{{BASE_URL}}";
const LOG_EXTS = ["sysbench.txt", "iostat.txt", "vmstat.txt", "dstat.txt",
                  "mpstat.txt", "innodb.txt", "mutex_metrics.csv", "pt-pmp.txt",
                  "stat-thpool.txt", "stat-thr.txt"];
const TIER_EXTS = ["cnf.txt", "vars.txt", "status.txt", "pt-mysql-summary.txt"];

function serverToPath(server) {
  // Handle formats: "Percona-Server 9.7.1-1", "run2-Percona-Server-9.7.1-1"
  let cleanServer = server.replace(/^run\d+-/, '');

  let idx = cleanServer.indexOf(' ');
  if (idx === -1) {
    // No space: split at the hyphen before the version (starts with digits)
    const match = cleanServer.match(/^(.+?)-(\d+\.\d+\..+)$/);
    if (match) {
      return match[1] + '/' + match[2];
    }
    idx = cleanServer.indexOf('-');
  }
  return cleanServer.slice(0, idx) + '/' + cleanServer.slice(idx + 1);
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

function showDownloadModal(d) {
  const server = d.server, mem_gb = d.mem_gb, threads = d.threads, tps = d.tps, qps = d.qps;
  const path = serverToPath(server);
  // Thread pool token used in file names: "tpoff" or "tp<SIZE>_os<OVERSUB>"
  const tpToken = d.tp === "off" ? "tpoff" : `tp${d.tp}_os${d.os}`;

  // Extract run number if server name includes it (e.g., "run2-Percona-Server-9.7.1-1")
  const runMatch = server.match(/^run(\d+)-/);
  const isIndividualRun = !!runMatch;
  const runNum = runMatch ? runMatch[1] : '1';

  const displayServer = server.replace(/^run\d+-/, '');

  document.getElementById('dlTitle').textContent = displayServer;
  document.getElementById('dlSubtitle').textContent =
    isIndividualRun
      ? `Run: ${runNum}  ·  Rows: ${d.rows}  ·  Buffer pool: ${mem_gb}G  ·  Thread pool: ${tpLabel(d.tp, d.os)}  ·  Threads: ${threads}  ·  TPS: ${tps.toLocaleString()}  ·  QPS: ${qps.toLocaleString()}`
      : `Rows: ${d.rows}  ·  Buffer pool: ${mem_gb}G  ·  Thread pool: ${tpLabel(d.tp, d.os)}  ·  Threads: ${threads}  ·  Average TPS: ${tps.toLocaleString()}  ·  Average QPS: ${qps.toLocaleString()}`;
  const list = document.getElementById('dlLinks');
  list.innerHTML = '';

  // For individual runs, show run-specific files; for average view, only per-tier files
  if (isIndividualRun) {
    const fileBase = d.file || `run${runNum}_${d.rows}_Tier${mem_gb}G_${tpToken}_RW_${threads}th`;
    LOG_EXTS.forEach(ext => {
      const fname = `${fileBase}.${ext}`;
      const url = `${BASE_URL}/${path}/${fname}`;
      list.appendChild(makeListItem(ext, fname, url));
    });
  }

  // Per-tier files (always shown)
  TIER_EXTS.forEach(ext => {
    const fname = ext === "pt-mysql-summary.txt"
      ? `Tier${mem_gb}G_${tpToken}-${ext}`
      : `Tier${mem_gb}G_${tpToken}.${ext}`;
    const url = `${BASE_URL}/${path}/${fname}`;
    list.appendChild(makeListItem(ext, fname, url));
  });
  // The error log is shared by all thread pool configurations of a tier
  list.appendChild(makeListItem("errlog.txt", `Tier${mem_gb}G.errlog.txt`,
                                `${BASE_URL}/${path}/Tier${mem_gb}G.errlog.txt`));

  document.getElementById('dlOverlay').classList.add('open');
}

function closeModal() {
  document.getElementById('dlOverlay').classList.remove('open');
}

function attachPlotlyClick() {
  const chartDiv = document.getElementById('chart');
  chartDiv.removeAllListeners('plotly_click');
  chartDiv.on('plotly_click', function(eventData) {
    if (!eventData || !eventData.points || !eventData.points.length) return;
    const pt = eventData.points[0];
    const d = pt.customdata;
    if (!d) return;
    showDownloadModal(d);
  });
}

// URL parameters, e.g. ?display=table&mem=2,32&tp=off,80&os=2,3
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
  if (display === "graph" || display === "table") {
    DISPLAY_MODE = display;
    const radio = document.querySelector(`input[name="displayMode"][value="${display}"]`);
    if (radio) radio.checked = true;
  }

  // Accept "4" and "4G" alike for the buffer pool
  applyListParam(params, "mem", el("memSel"), MEMS, s => s.replace(/[Gg]$/, ""));
  applyListParam(params, "tp", el("tpSel"), TP_SIZES, s => s.toLowerCase());
  applyListParam(params, "os", el("osSel"), OS_VALUES, s => s);
}

function syncUrl() {
  const params = new URLSearchParams(window.location.search);
  params.set("display", DISPLAY_MODE);
  const mems = getSelectedValues(el("memSel"));
  params.set("mem", mems.length === MEMS.length ? "all" : mems.join(","));
  const tps = getSelectedValues(el("tpSel"));
  params.set("tp", tps.length === TP_SIZES.length ? "all" : tps.join(","));
  const osVals = getSelectedValues(el("osSel"));
  params.set("os", osVals.length === OS_VALUES.length ? "all" : osVals.join(","));
  try {
    history.replaceState(null, "", `${window.location.pathname}?${params}`);
  } catch (e) { /* file:// in some browsers forbids replaceState */ }
}

function render() {
  syncUrl();
  const graph = DISPLAY_MODE === "graph";
  el("chart").style.display = graph ? "" : "none";
  el("tableView").style.display = graph ? "none" : "";
  if (graph) {
    Plotly.react("chart", buildTraces(), layoutForMode(), {responsive: true})
      .then(attachPlotlyClick);
  } else {
    buildTable();
  }
}

function refreshServers() {
  fillOptions(el("serverSel"), serverList());
  setSelected(el("serverSel"), _ => true);
}

function init() {
  refreshServers();
  fillOptions(el("memSel"), MEMS, (v)=>`${v}G`);
  fillOptions(el("tpSel"), TP_SIZES, (v)=>v === "off" ? "off (no thread pool)" : v);
  fillOptions(el("osSel"), OS_VALUES);

  // Default buffer pool tier; all thread pool sizes and oversubscribe values
  setSelected(el("memSel"), v => Number(v) === DEFAULT_MEM);
  setSelected(el("tpSel"), _ => true);
  setSelected(el("osSel"), _ => true);

  ["serverSel","memSel","tpSel","osSel"].forEach(id => {
    el(id).addEventListener("change", render);
  });

  document.querySelectorAll('input[name="viewMode"]').forEach(radio => {
    radio.addEventListener("change", () => {
      VIEW_MODE = radio.value;
      DATA = computeData();
      refreshServers();
      render();
    });
  });

  document.querySelectorAll('input[name="displayMode"]').forEach(radio => {
    radio.addEventListener("change", () => {
      DISPLAY_MODE = radio.value;
      render();
    });
  });

  el("allServersBtn").addEventListener("click", () => { setSelected(el("serverSel"), _ => true); render(); });
  el("allMemsBtn").addEventListener("click", () => { setSelected(el("memSel"), _ => true); render(); });
  el("allTpsBtn").addEventListener("click", () => { setSelected(el("tpSel"), _ => true); render(); });
  el("allOsBtn").addEventListener("click", () => { setSelected(el("osSel"), _ => true); render(); });
  el("resetBtn").addEventListener("click", () => {
    setSelected(el("serverSel"), _ => true);
    setSelected(el("memSel"), v => Number(v) === DEFAULT_MEM);
    setSelected(el("tpSel"), _ => true);
    setSelected(el("osSel"), _ => true);
    render();
  });

  // Radios are unaffected by page reload state; sync modes with the checked ones
  const checked = document.querySelector('input[name="viewMode"]:checked');
  if (checked) VIEW_MODE = checked.value;
  const checkedDisplay = document.querySelector('input[name="displayMode"]:checked');
  if (checkedDisplay) DISPLAY_MODE = checkedDisplay.value;
  DATA = computeData();
  refreshServers();

  applyUrlParams();
  render();
}

window.addEventListener('load', function() { loadPlotly(init); });
</script>

<!-- Download modal -->
<div id="dlOverlay">
  <div id="dlModal">
    <button id="dlClose" title="Close">&#x2715;</button>
    <h3 id="dlTitle">Download log files</h3>
    <div id="dlSubtitle" class="subtitle"></div>
    <ul id="dlLinks" class="dl-list"></ul>
  </div>
</div>

<script>
document.getElementById('dlClose').addEventListener('click', closeModal);
document.getElementById('dlOverlay').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeModal();
});
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive sysbench thread pool HTML report from benchmark_logs."
    )
    parser.add_argument("--base-dir", default="benchmark_logs",
                        help="Directory with <server>/<version>/ benchmark logs (default: benchmark_logs)")
    parser.add_argument("--output", default="benchmark_report.html",
                        help="Output HTML file (default: benchmark_report.html)")
    parser.add_argument("--test-type", default="OLTP Read-Write",
                        help='Test type label shown in the report (default: "OLTP Read-Write")')
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    if not base_dir.is_dir():
        sys.exit(f"base_dir not found: {base_dir}")

    output_file = Path(args.output)

    print(f"Scanning: {base_dir}")

    rows, durations = scan_runs(base_dir)
    if not rows:
        sys.exit(f"No valid sysbench data found under '{base_dir}'")

    run_counts = Counter((r["server"], r["mem_gb"], str(r["tp"]), r["os"], r["threads"])
                         for r in rows)
    max_runs = max(run_counts.values())
    for (server, mem, tp, osub, threads), count in sorted(
            run_counts.items(), key=lambda kv: [str(x) for x in kv[0]]):
        if count < max_runs:
            tp_label = "off" if tp == "off" else f"{tp} os{osub}"
            print(
                f"  warning: only {count}/{max_runs} run(s) for "
                f"{server} mem={mem}G tp={tp_label} threads={threads}",
                file=sys.stderr,
            )

    data_block, servers, mems, threads, tp_sizes, os_values = build_data_block(rows)

    # System info table from pt-summary output
    sys_info = parse_pt_summary(base_dir)
    sys_pairs = [(k, sys_info.get(k, "n/a"))
                 for k in PT_SUMMARY_KEYS + ["Memory Total"]]

    # Run configuration table
    rows_values = sorted({r["rows"] for r in rows})
    config_pairs = [("Test Type", args.test_type), ("Tables", "20"),
                    ("Rows per table", ", ".join(rows_values))]
    if durations:
        common = Counter(round(d) for d in durations).most_common(1)[0][0]
        config_pairs.append(("Test duration", f"{common}s"))
    config_pairs.append(
        ("Runs per configuration", f"{max_runs} (switch between averaged and individual runs above)")
    )

    # About table
    about_pairs = [
        ("Servers compared", ", ".join(servers)),
        ("Buffer pool tiers (innodb_buffer_pool_size)",
         ", ".join(f"{m} GB" for m in mems)),
        ("Thread pool sizes (thread_pool_size)",
         ", ".join(str(t) for t in tp_sizes)),
        ("Oversubscribe (thread_pool_oversubscribe)",
         ", ".join(str(o) for o in os_values)),
        ("Client threads", ", ".join(str(t) for t in threads)),
        ("Metric", "Transactions per second (TPS); QPS shown in hover tooltips"),
    ]

    out = (
        TEMPLATE
        .replace("{{DATA_BLOCK}}", data_block)
        .replace("{{BASE_URL}}", str(base_dir))
        .replace("{{TEST_TYPE}}", args.test_type)
        .replace("{{SYSTEM_INFO_ROWS}}", table_rows(sys_pairs))
        .replace("{{RUN_CONFIG_ROWS}}", table_rows(config_pairs))
        .replace("{{ABOUT_ROWS}}", table_rows(about_pairs))
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(out)

    print(f"Done. Report written to: {output_file}")
    print(f"  Servers      : {len(servers)}")
    print(f"  Buffer pools : {', '.join(str(m) for m in mems)}")
    print(f"  TP sizes     : {', '.join(str(t) for t in tp_sizes)}")
    print(f"  Oversubscribe: {', '.join(str(o) for o in os_values)}")
    print(f"  Threads      : {', '.join(str(t) for t in threads)}")
    print(f"  Records      : {len(rows)} individual runs (up to {max_runs} per configuration)")


if __name__ == "__main__":
    main()
