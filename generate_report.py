#!/usr/bin/env python3
"""
Scans benchmark_logs/<server>/<version>/ for run<R>_Tier<M>G_RW_<T>th.sysbench.txt
files, parses TPS/QPS, and generates a self-contained interactive Plotly HTML
report. The HTML template is embedded in this script; no external files needed.

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

# Matches run<N>_<ROWS>_Tier<M>G_RW_<T>th and the older run<N>_Tier<M>G_RW_<T>th
FILENAME_RE = re.compile(
    r"^run(?P<run>\d+)_(?:(?P<rows>\d+[KkMm]?)_)?Tier(?P<mem>\d+)G_RW_(?P<threads>\d+)th\.sysbench\.txt$"
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
    # The same run can exist under both its old-style name (no rows token,
    # meaning the 5M default) and its renamed new-style one; keep a single
    # entry per logical run, preferring the file with the explicit rows token.
    entries = {}
    for server, version, m, (tps, qps, duration) in iter_sysbench_files(base_dir):
        explicit_rows = m.group("rows") is not None
        key = (server, version, int(m.group("run")),
               m.group("rows") or "5M", int(m.group("mem")), int(m.group("threads")))
        prev = entries.get(key)
        if prev is not None:
            old_name = m.string if prev["explicit_rows"] else prev["row"]["file"] + ".sysbench.txt"
            print(f"  duplicate run (old-style name ignored): {server}/{version}/{old_name}",
                  file=sys.stderr)
            if prev["explicit_rows"]:
                continue
        entries[key] = {
            "explicit_rows": explicit_rows,
            "duration": duration,
            "row": {
                "server": f"{server} {version}",
                "run": int(m.group("run")),
                # Old-style file names carry no rows token; those runs used the 5M default
                "rows": m.group("rows") or "5M",
                "file": m.string[: -len(".sysbench.txt")],
                "mem_gb": int(m.group("mem")),
                "threads": int(m.group("threads")),
                "tps": round(tps, 2),
                "qps": round(qps, 2),
            },
        }
    rows = [e["row"] for e in entries.values()]
    durations = [e["duration"] for e in entries.values() if e["duration"]]
    rows.sort(key=lambda r: (r["server"], r["run"], r["rows"], r["mem_gb"], r["threads"]))
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


def rows_sort_key(label):
    """Order rows labels by their numeric value: 500K < 5M < 10M."""
    m = re.match(r"^(\d+)([KkMm]?)$", label)
    if not m:
        return (float("inf"), label)
    mult = {"": 1, "k": 1000, "m": 1000000}[m.group(2).lower()]
    return (int(m.group(1)) * mult, label)


def build_data_block(rows):
    servers_sorted = sorted({r["server"] for r in rows})
    mems_sorted = sorted({r["mem_gb"] for r in rows})
    threads_sorted = sorted({r["threads"] for r in rows})
    rows_values = sorted({r["rows"] for r in rows}, key=rows_sort_key)

    block = (
        f"const RUNS = {json.dumps(rows)};\n"
        f"const MEMS = {json.dumps(mems_sorted)};\n"
        f"const THREADS = {json.dumps(threads_sorted)};\n"
        f"const ROWS_VALUES = {json.dumps(rows_values)};"
    )
    return block, servers_sorted, mems_sorted, threads_sorted, rows_values


TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Sysbench Interactive Comparison</title>
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
    select[multiple] { height: 300px; }
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
  <h2>Sysbench Performance &mdash; Interactive Comparison</h2>
  <div class="wrap">
    <div class="card">
      <label for="serverSel">Servers (multi-select)</label>
      <select id="serverSel" multiple></select>
      <div class="hint">Tip: Ctrl/Cmd-click to select multiple. Use "Select all".</div>

      <div class="row">
        <div>
          <label for="memSel">Memory (multi-select)</label>
          <select id="memSel" multiple></select>
        </div>
        <div>
          <label for="rowsSel">Table rows (multi-select)</label>
          <select id="rowsSel" multiple></select>
        </div>
      </div>

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
        <button id="allServersBtn">Select all servers</button>
        <button id="allMemsBtn">Select all memory</button>
        <button id="allRowsBtn">Select all rows</button>
        <button id="resetBtn">Reset</button>
      </div>

      <div class="hint">
        Chart overlays selected servers at selected memory values. Missing points are omitted automatically.
        Click a data point to download its log files.
        Shareable URL parameters: <code>?display=graph|table&amp;mem=4,32</code> (or <code>mem=all</code>).
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

const DEFAULT_MEM = MEMS.includes(12) ? 12 : MEMS[0];

let VIEW_MODE = "average";
let DISPLAY_MODE = "graph";

function computeData() {
  if (VIEW_MODE === "individual") {
    return RUNS.map(r => ({
      server: `run${r.run}-${r.server.replace(/ /g, "-")}`,
      rows: r.rows, mem_gb: r.mem_gb, threads: r.threads, tps: r.tps, qps: r.qps,
      file: r.file,
    }));
  }
  // Average all runs per (server, rows, mem, threads)
  const groups = new Map();
  RUNS.forEach(r => {
    const key = `${r.server}|${r.rows}|${r.mem_gb}|${r.threads}`;
    let g = groups.get(key);
    if (!g) {
      g = {server: r.server, rows: r.rows, mem_gb: r.mem_gb, threads: r.threads, tps: [], qps: []};
      groups.set(key, g);
    }
    g.tps.push(r.tps);
    g.qps.push(r.qps);
  });
  const mean = a => a.reduce((s, v) => s + v, 0) / a.length;
  return Array.from(groups.values()).map(g => ({
    server: g.server, rows: g.rows, mem_gb: g.mem_gb, threads: g.threads,
    tps: Math.round(mean(g.tps) * 100) / 100,
    qps: Math.round(mean(g.qps) * 100) / 100,
  }));
}

let DATA = computeData();

function serverList() {
  return [...new Set(DATA.map(r => r.server))].sort();
}

// Series matching the current selection: one per (server, mem, rows) with data.
// Shared by the graph and the table views.
function selectedSeries() {
  const selectedServers = getSelectedValues(el("serverSel"));
  const selectedMems = getSelectedValues(el("memSel")).map(numeric).filter(v => v !== null);
  const selectedRows = getSelectedValues(el("rowsSel"));

  const servers = selectedServers.length ? selectedServers : [serverList()[0]];
  const mems = selectedMems.length ? selectedMems : [DEFAULT_MEM];
  const rowsVals = selectedRows.length ? selectedRows : ROWS_VALUES;

  const series = [];
  servers.forEach(server => {
    mems.forEach(mem => {
      rowsVals.forEach(rows => {
        const pts = DATA
          .filter(r => r.server === server && r.mem_gb === mem && r.rows === rows && r.tps !== null)
          .sort((a,b)=>a.threads-b.threads);

        if (!pts.length) return;
        series.push({ name: `${server} | ${mem}G | ${rows} rows`, pts });
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
    customdata: s.pts.map(p=>({server: p.server, rows: p.rows, mem_gb: p.mem_gb, threads: p.threads, tps: p.tps, qps: p.qps, file: p.file})),
    marker: { size: 10 },
    hovertemplate:
      '<b>%{customdata.server}</b><br>' +
      'Rows: %{customdata.rows}<br>' +
      'Memory: %{customdata.mem_gb}G<br>' +
      'Threads: %{customdata.threads}<br>' +
      'TPS: %{customdata.tps:,.0f}<br>' +
      'QPS: %{customdata.qps:,.0f}' +
      '<extra></extra>',
  }));
}

// TPS table: one row per (server, mem, rows) series, one column per thread count.
// Cells open the same download modal as clicking a graph point.
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
  nameTh.textContent = "Server | Memory | Rows";
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
        td.textContent = "\\u2014";
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
    legend: {
      x: 0.02, y: 0.98,
      xanchor: "left", yanchor: "top",
      bgcolor: "rgba(255,255,255,0.75)",
      bordercolor: "#ddd", borderwidth: 1,
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
                  "innodb.txt", "pt-pmp.txt",
                  "stat-thpool.txt", "stat-thr.txt"];
const TIER_EXTS = ["status.txt", "vars.txt", "cnf.txt", "errlog.txt", "pt-mysql-summary.txt"];

function serverToPath(server) {
  // Handle formats: "Percona-Server 8.4.10-10", "run2-Percona-Server-8.4.10-10"
  let cleanServer = server.replace(/^run\\d+-/, '');

  let idx = cleanServer.indexOf(' ');
  if (idx === -1) {
    // No space: split at the hyphen before the version (starts with digits)
    const match = cleanServer.match(/^(.+?)-(\\d+\\.\\d+\\..+)$/);
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

  // Extract run number if server name includes it (e.g., "run2-Percona-Server-8.4.10-10")
  const runMatch = server.match(/^run(\\d+)-/);
  const isIndividualRun = !!runMatch;
  const runNum = runMatch ? runMatch[1] : '1';

  const displayServer = server.replace(/^run\\d+-/, '');

  document.getElementById('dlTitle').textContent = displayServer;
  document.getElementById('dlSubtitle').textContent =
    isIndividualRun
      ? `Run: ${runNum}  ·  Rows: ${d.rows}  ·  Memory: ${mem_gb}G  ·  Threads: ${threads}  ·  TPS: ${tps.toLocaleString()}  ·  QPS: ${qps.toLocaleString()}`
      : `Rows: ${d.rows}  ·  Memory: ${mem_gb}G  ·  Threads: ${threads}  ·  Average TPS: ${tps.toLocaleString()}  ·  Average QPS: ${qps.toLocaleString()}`;
  const list = document.getElementById('dlLinks');
  list.innerHTML = '';

  // For individual runs, show run-specific files; for average view, only per-tier files
  if (isIndividualRun) {
    const fileBase = d.file || `run${runNum}_Tier${mem_gb}G_RW_${threads}th`;
    LOG_EXTS.forEach(ext => {
      const fname = `${fileBase}.${ext}`;
      const url = `${BASE_URL}/${path}/${fname}`;
      list.appendChild(makeListItem(ext, fname, url));
    });
  }

  // Per-tier files (always shown)
  TIER_EXTS.forEach(ext => {
    const fname = ext === "pt-mysql-summary.txt" ? `Tier${mem_gb}G-${ext}` : `Tier${mem_gb}G.${ext}`;
    const url = `${BASE_URL}/${path}/${fname}`;
    list.appendChild(makeListItem(ext, fname, url));
  });

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

// URL parameters: ?display=graph|table & mem=<tier>[,<tier>...]|all (e.g. ?display=table&mem=4,32)
function applyUrlParams() {
  const params = new URLSearchParams(window.location.search);

  const display = params.get("display");
  if (display === "graph" || display === "table") {
    DISPLAY_MODE = display;
    const radio = document.querySelector(`input[name="displayMode"][value="${display}"]`);
    if (radio) radio.checked = true;
  }

  const mem = params.get("mem");
  if (mem) {
    if (mem.toLowerCase() === "all") {
      setSelected(el("memSel"), _ => true);
    } else {
      // Accept "4" and "4G" alike
      const wanted = new Set(mem.split(",").map(s => s.trim().replace(/[Gg]$/, "")));
      if (MEMS.some(m => wanted.has(String(m)))) {
        setSelected(el("memSel"), v => wanted.has(v));
      }
    }
  }
}

function syncUrl() {
  const params = new URLSearchParams(window.location.search);
  params.set("display", DISPLAY_MODE);
  const mems = getSelectedValues(el("memSel"));
  params.set("mem", mems.length === MEMS.length ? "all" : mems.join(","));
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
  fillOptions(el("rowsSel"), ROWS_VALUES);

  // Default memory tier and all rows values selected by default
  setSelected(el("memSel"), v => Number(v) === DEFAULT_MEM);
  setSelected(el("rowsSel"), _ => true);

  ["serverSel","memSel","rowsSel"].forEach(id => {
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
  el("allRowsBtn").addEventListener("click", () => { setSelected(el("rowsSel"), _ => true); render(); });
  el("resetBtn").addEventListener("click", () => {
    setSelected(el("serverSel"), _ => true);
    setSelected(el("memSel"), v => Number(v) === DEFAULT_MEM);
    setSelected(el("rowsSel"), _ => true);
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
        description="Generate an interactive sysbench HTML report from benchmark_logs."
    )
    parser.add_argument("--base-dir", default="benchmark_logs",
                        help="Directory with <server>/<version>/ benchmark logs (default: benchmark_logs)")
    parser.add_argument("--output", default=None,
                        help="Output HTML file (default: <base-dir>/../sysbench_interactive_comparison.html)")
    parser.add_argument("--test-type", default="OLTP Read-Write",
                        help='Test type label shown in the report (default: "OLTP Read-Write")')
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    if not base_dir.is_dir():
        sys.exit(f"base_dir not found: {base_dir}")

    output_file = (
        Path(args.output) if args.output
        else base_dir.resolve().parent / "sysbench_interactive_comparison.html"
    )

    print(f"Scanning: {base_dir}")

    rows, durations = scan_runs(base_dir)
    if not rows:
        sys.exit(f"No valid sysbench data found under '{base_dir}'")

    run_counts = Counter((r["server"], r["rows"], r["mem_gb"], r["threads"]) for r in rows)
    max_runs = max(run_counts.values())
    for (server, rows_label, mem, threads), count in sorted(run_counts.items()):
        if count < max_runs:
            print(
                f"  warning: only {count}/{max_runs} run(s) for "
                f"{server} rows={rows_label} mem={mem}G threads={threads}",
                file=sys.stderr,
            )

    data_block, servers, mems, threads, rows_values = build_data_block(rows)

    # System info table from pt-summary output
    sys_info = parse_pt_summary(base_dir)
    sys_pairs = [(k, sys_info.get(k, "n/a"))
                 for k in PT_SUMMARY_KEYS + ["Memory Total"]]

    # Run configuration table
    config_pairs = [("Test Type", args.test_type), ("Tables", "20")]
    if durations:
        common = Counter(round(d) for d in durations).most_common(1)[0][0]
        config_pairs.append(("Test duration", f"{common}s"))
    config_pairs.append(
        ("Runs per configuration", f"{max_runs} (switch between averaged and individual runs above)")
    )

    # About table
    about_pairs = [
        ("Servers compared", ", ".join(servers)),
        ("Memory tiers (innodb_buffer_pool_size)",
         ", ".join(f"{m} GB" for m in mems)),
        ("Table rows variants", ", ".join(rows_values)),
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
    print(f"  Servers : {len(servers)}")
    print(f"  Memories: {', '.join(str(m) for m in mems)}")
    print(f"  Rows    : {', '.join(rows_values)}")
    print(f"  Threads : {', '.join(str(t) for t in threads)}")
    print(f"  Records : {len(rows)} individual runs (up to {max_runs} per configuration)")


if __name__ == "__main__":
    main()
