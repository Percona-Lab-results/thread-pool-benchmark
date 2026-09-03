#!/bin/bash

# Check if required tools are installed
if ! command -v sysbench >/dev/null 2>&1; then
    echo "Error: sysbench is not installed." >&2
    exit 1
fi

if ! command -v iostat >/dev/null 2>&1; then
    echo "Error: sysstat (iostat) is not installed." >&2
    exit 1
fi

if ! command -v dstat >/dev/null 2>&1; then
    echo "Error: dstat is not installed." >&2
    exit 1
fi

# Optional: Install prerequisites if not present
# sudo apt update
# sudo apt install linux-tools-generic sysstat sysbench dstat -y

# --- CLEANUP: kill leftover background processes from previous runs ---
echo "Killing leftover benchmark processes (if any)..."
sudo killall -q mysqld sysbench iostat vmstat mpstat dstat pt-pmp 2>/dev/null
sudo pkill -f 'collect_lru_metrics\.sh' 2>/dev/null
sudo pkill -f 'collect_mutex_metrics\.sh' 2>/dev/null

# Give mysqld a chance to shut down gracefully (SIGTERM), force kill after 60s
WAITED=0
while pgrep -x mysqld >/dev/null; do
    if [ "$WAITED" -ge 60 ]; then
        echo "mysqld still running after ${WAITED}s, sending SIGKILL..."
        sudo killall -9 -q mysqld 2>/dev/null
        sleep 2
        break
    fi
    echo "Waiting for leftover mysqld to shut down... (${WAITED}s)"
    sleep 2
    WAITED=$(( WAITED + 2 ))
done

# Remove stale PID files from previous runs
rm -f /tmp/mysql_benchmark.pid /tmp/iostat.pid /tmp/vmstat.pid /tmp/mpstat.pid /tmp/dstat.pid \
      /tmp/innodb.pid /tmp/lru_metrics.pid /tmp/mutex_metrics.pid /tmp/gdb.pid \
      /tmp/thread_status_thpool.pid /tmp/thread_status_thr.pid
echo "Cleanup done."

./run_pt.sh

echo ""
echo "=========================================================================="
echo "Starting benchmarks: $*"
echo "=========================================================================="
./run_metrics.sh "$@"

echo ""
echo "=========================================================================="
echo "All benchmarks completed!"
echo "=========================================================================="
echo ""
echo "Results saved to:"
echo "  - benchmark_logs/ (binlog disabled)"
echo "  - benchmark_logs_binlog/ (binlog enabled)"
echo ""
echo "Next steps:"
echo "  1. Generate reports:"
echo "     bash visuals/generate_both_reports.sh"
echo "  2. Generate InnoDB metrics reports:"
echo "     python3 visuals/innodb_metrics_report.py benchmark_logs innodb_metrics_report.html"
echo "     python3 visuals/innodb_metrics_report.py benchmark_logs_binlog innodb_metrics_report_binlog.html _binlog"
echo "  3. Generate variable comparisons:"
echo "     python3 visuals/generate_variable_comparisons.py benchmark_logs"
echo "     python3 visuals/generate_variable_comparisons.py benchmark_logs_binlog \"Binlog Enabled\""
echo "=========================================================================="