#!/usr/bin/env python3
"""
Thins mpstat capture files in place: keeps only one record (interval block)
for every N seconds and drops the rest.

Walks all sub-directories of --base-dir (default: benchmark_logs) looking for
*.mpstat.txt files produced by "mpstat -P ALL 1". Each file consists of a
banner, then one record per second (a header line, the "all" aggregate line,
one line per CPU, and a trailing blank line), optionally followed by an
"Average:" summary block. The banner and the Average block are always kept.

With mpstat's 1-second interval, keeping every Nth record keeps one record
per N seconds. Running the script again thins the remaining records further
(e.g. running twice with -n 10 leaves one record per 100 seconds).

Usage:
    python3 filter_mpstat.py [--base-dir=benchmark_logs] [-n 10] [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path

# Start of a record: "02:53:34 AM  CPU    %usr ..." (12/24-hour clock alike)
HEADER_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(?: [AP]M)?\s+CPU\s")


def filter_text(text, every):
    """Return (filtered_text, total_records, kept_records)."""
    out = []
    rec = -1
    kept = 0
    keep = True   # preamble (banner) is kept
    for line in text.splitlines(keepends=True):
        if HEADER_RE.match(line):
            rec += 1
            keep = (rec % every == 0)
            if keep:
                kept += 1
        elif line.startswith("Average"):
            keep = True
        if keep:
            out.append(line)
    return "".join(out), rec + 1, kept


def main():
    parser = argparse.ArgumentParser(
        description="Thin mpstat capture files: keep one record per N seconds.")
    parser.add_argument("--base-dir", default="benchmark_logs", type=Path,
                        help="Directory scanned recursively for *.mpstat.txt (default: benchmark_logs)")
    parser.add_argument("-n", "--every", type=int, default=10,
                        help="Keep one record for every N seconds (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report what would change; do not rewrite files")
    args = parser.parse_args()

    if args.every < 1:
        sys.exit("ERROR: --every must be >= 1")
    if not args.base_dir.is_dir():
        sys.exit(f"ERROR: base dir not found: {args.base_dir}")

    files = sorted(args.base_dir.rglob("*.mpstat.txt"))
    if not files:
        sys.exit(f"No *.mpstat.txt files found under {args.base_dir}")

    total_before = total_after = 0
    changed = skipped = 0
    for f in files:
        text = f.read_text(errors="replace")
        filtered, records, kept = filter_text(text, args.every)
        if records == 0:
            print(f"  skipped (no mpstat records): {f}", file=sys.stderr)
            skipped += 1
            continue
        total_before += len(text)
        total_after += len(filtered)
        if len(filtered) < len(text):
            changed += 1
            if not args.dry_run:
                tmp = f.with_suffix(f.suffix + ".tmp")
                tmp.write_text(filtered)
                tmp.replace(f)
        print(f"  {f}: {records} -> {kept} records, "
              f"{len(text) / 1024:.0f} KiB -> {len(filtered) / 1024:.0f} KiB")

    mb = 1024 * 1024
    action = "would shrink" if args.dry_run else "rewrote"
    print(f"\n{action.capitalize()} {changed} of {len(files)} files "
          f"({skipped} skipped): {total_before / mb:.0f} MiB -> "
          f"{total_after / mb:.0f} MiB "
          f"({100 * (1 - total_after / max(total_before, 1)):.1f}% saved)")


if __name__ == "__main__":
    main()
