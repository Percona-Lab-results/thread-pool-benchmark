#!/bin/bash
# MySQL/Percona Server Benchmark Script with Metrics Collection
#
# Usage: ./run_metrics.sh --server-dir=<path>
#                         [--table-rows=<n>[K|M]] [--warmup=<seconds>] [--duration=<seconds>]
#                         [--thread-list=<n,n,...>] [--pool-size-list=<n,n,...>] [--cpu-freq=<MHz>]
#                         [--runs=<n>] [--run-start=<n>] [--datadir=<path>]
#
# Arguments:
#   --server-dir=<path>  - (Required) Path to the server installation directory.
#                          DBMS name and version are detected from the directory name,
#                          e.g. .../Percona-Server-8.4.10-10-Linux.x86_64.glibc2.35
#                          gives name "Percona-Server" and version "8.4.10-10".
#   --table-rows=<n>     - (Optional) Rows per table (default: 5M).
#                          Supports K (thousands) and M (millions) suffixes, e.g. 500K, 5M.
#                          The value as passed is used in result file names: run<N>_<ROWS>_*
#   --warmup=<seconds>   - (Optional) Read-write warmup time in seconds (default: 600)
#   --duration=<seconds> - (Optional) Benchmark duration in seconds (default: 900)
#   --thread-list=<list> - (Optional) Comma-separated sysbench thread counts
#                          (default: 40,80,120,160,320,640,1280,2560)
#   --pool-size-list=<list> - (Optional) Comma-separated buffer pool sizes in GB
#                          (default: 2,12,32)
#   --cpu-freq=<MHz>     - (Optional) CPU frequency in MHz to pin all cores to (default: 2400)
#   --runs=<n>           - (Optional) Number of runs per iteration (default: 1)
#   --run-start=<n>      - (Optional) Start run number (default: 1); each run prepends
#                          "run<N>_" to the results file names
#   --datadir=<path>     - (Optional) Base directory for MySQL data directories; must be
#                          on NVMe storage (default: /home/bogdan.degtyariov/servers/data)
#
# Thread pool sweep (per buffer pool tier, when the server supports a thread pool):
#   - disabled (thread_handling = one-thread-per-connection)
#   - thread_pool_size in {40, 80, 120, 160}, each with thread_pool_oversubscribe in {2, 3, 4}
#
# Examples:
#   ./run_metrics.sh --server-dir=~/servers/Percona-Server-8.4.10-10-Linux.x86_64.glibc2.35
#   ./run_metrics.sh --server-dir=~/servers/mysql-9.7.0-linux-glibc2.28-x86_64
#   ./run_metrics.sh --server-dir=... --table-rows=500K --warmup=60 --duration=120
#   ./run_metrics.sh --server-dir=... --thread-list=32,64,128 --pool-size-list=12

# --- VARIABLES ---
DB_HOST="127.0.0.1"
DB_USER="root"
DB_PASS="password"
DB_DATABASE="sbtest"
DB_PORT="3306"

# Server locations, overridden by --datadir
DATADIR_BASE="/home/bogdan.degtyariov/servers/data"

# Buffer pool tiers (GB), overridden by --pool-size-list
POOL_SIZES=(2 12 32)

# Sysbench thread counts, overridden by --thread-list
THREADS=(40 80 120 160 320 640 1280 2560)

# Thread pool sweep values (thread_pool_size x thread_pool_oversubscribe)
TP_SIZES=(40 80 120 160)
TP_OVERSUBS=(2 3 4)

# --- DEBUG SETTINGS ---
TABLE_ROWS=5000000
TABLE_ROWS_LABEL="5M"   # used in result file names: run<N>_<ROWS>_*
WARMUP_TIME=600
DURATION=900

# --- ARGUMENT PARSING ---
usage() {
    echo "Usage: $0 --server-dir=<path>" >&2
    echo "          [--table-rows=<n>[K|M]] [--warmup=<seconds>] [--duration=<seconds>]" >&2
    echo "          [--thread-list=<n,n,...>] [--pool-size-list=<n,n,...>] [--cpu-freq=<MHz>]" >&2
    echo "          [--runs=<n>] [--run-start=<n>] [--datadir=<path>]" >&2
    exit 1
}

# Plain integer with optional K (thousands) or M (millions) suffix, e.g. 500K, 5M
parse_rows() {
    if [[ "$1" =~ ^([0-9]+)([KkMm]?)$ ]]; then
        local NUM="${BASH_REMATCH[1]}"
        case "${BASH_REMATCH[2]}" in
            K|k) echo $(( NUM * 1000 )) ;;
            M|m) echo $(( NUM * 1000000 )) ;;
            *)   echo "$NUM" ;;
        esac
    else
        echo "ERROR: Invalid row count: $1 (expected e.g. 5000000, 500K or 5M)" >&2
        exit 1
    fi
}

parse_uint() {
    if [[ "$1" =~ ^[0-9]+$ ]]; then
        echo "$1"
    else
        echo "ERROR: Invalid ${2:-number}: $1" >&2
        exit 1
    fi
}

# Comma-separated list of integers, e.g. 32,64,128 -> "32 64 128"
parse_int_list() {
    if [[ "$1" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        echo "${1//,/ }"
    else
        echo "ERROR: Invalid list: $1 (expected comma-separated integers, e.g. 32,64,128)" >&2
        exit 1
    fi
}

SERVER_DIR=""
CPU_FREQ_MHZ="2400"
NUM_RUNS="1"
RUN_START="1"

for arg in "$@"; do
    case "$arg" in
        --server-dir=*)  SERVER_DIR="${arg#*=}" ;;
        --datadir=*)     DATADIR_BASE="${arg#*=}" ;;
        --table-rows=*)  TABLE_ROWS=$(parse_rows "${arg#*=}") || exit 1; TABLE_ROWS_LABEL="${arg#*=}" ;;
        --warmup=*)      WARMUP_TIME=$(parse_uint "${arg#*=}" "number of seconds") || exit 1 ;;
        --duration=*)    DURATION=$(parse_uint "${arg#*=}" "number of seconds") || exit 1 ;;
        --cpu-freq=*)    CPU_FREQ_MHZ=$(parse_uint "${arg#*=}" "CPU frequency in MHz") || exit 1 ;;
        --runs=*)        NUM_RUNS=$(parse_uint "${arg#*=}" "number of runs") || exit 1 ;;
        --run-start=*)   RUN_START=$(parse_uint "${arg#*=}" "start run number") || exit 1 ;;
        --thread-list=*)    LIST=$(parse_int_list "${arg#*=}") || exit 1; THREADS=($LIST) ;;
        --pool-size-list=*) LIST=$(parse_int_list "${arg#*=}") || exit 1; POOL_SIZES=($LIST) ;;
        -h|--help)       usage ;;
        *) echo "ERROR: Unknown argument: $arg" >&2; usage ;;
    esac
done

if [ -z "$SERVER_DIR" ]; then
    echo "ERROR: --server-dir is required" >&2
    usage
fi

SERVER_DIR="${SERVER_DIR%/}"
if [ ! -d "$SERVER_DIR" ]; then
    echo "ERROR: Server directory not found: $SERVER_DIR" >&2
    exit 1
fi

DATADIR_BASE="${DATADIR_BASE%/}"
if [ -z "$DATADIR_BASE" ]; then
    echo "ERROR: --datadir requires a non-empty path" >&2
    exit 1
fi

# --- DETECT DBMS NAME & VERSION FROM SERVER DIR ---
# e.g. Percona-Server-8.4.10-10-Linux.x86_64.glibc2.35 -> name "Percona-Server", version "8.4.10-10"
#      mysql-9.7.0-linux-glibc2.28-x86_64             -> name "mysql", version "9.7.0"
DIR_BASE=$(basename "$SERVER_DIR")
STRIPPED="${DIR_BASE%%-[Ll]inux*}"
if [[ "$STRIPPED" =~ ^([A-Za-z][A-Za-z_-]*)-([0-9][0-9.-]*)$ ]]; then
    DBMS_NAME="${BASH_REMATCH[1]}"
    DBMS_VER="${BASH_REMATCH[2]}"
else
    echo "ERROR: Cannot detect DBMS name/version from directory name: $DIR_BASE" >&2
    exit 1
fi

ADMIN_TOOL="mysqladmin"

# --- THREAD POOL SWEEP CONFIGS ---
# Each entry is "off" or "<thread_pool_size>x<thread_pool_oversubscribe>"
TP_SUPPORTED="0"
if [[ "${DBMS_NAME,,}" == percona* ]]; then
    TP_SUPPORTED="1"
elif [[ "${DBMS_NAME,,}" == mysql ]] && \
     [ "$(printf '%s\n' 26.7.0 "$DBMS_VER" | sort -V | head -n1)" == "26.7.0" ]; then
    TP_SUPPORTED="1"
fi

TP_CONFIGS=("off")
if [ "$TP_SUPPORTED" == "1" ]; then
    for TP_SIZE in "${TP_SIZES[@]}"; do
        for TP_OVERSUB in "${TP_OVERSUBS[@]}"; do
            TP_CONFIGS+=("${TP_SIZE}x${TP_OVERSUB}")
        done
    done
else
    echo "WARNING: thread pool not supported by ${DBMS_NAME} ${DBMS_VER}; running only the disabled configuration" >&2
fi

# "off" -> "tpoff", "40x2" -> "tp40_os2" (thread_pool_size 40, oversubscribe 2);
# used in result file names
tp_label() {
    if [ "$1" == "off" ]; then echo "tpoff"; else echo "tp${1%x*}_os${1#*x}"; fi
}

# --- CHECK DATADIR_BASE IS ON NVME STORAGE ---
mkdir -p "$DATADIR_BASE"
SRC_DEV=$(df --output=source "$DATADIR_BASE" 2>/dev/null | tail -n1)
if [[ "$SRC_DEV" != /dev/* ]]; then
    echo "ERROR: Cannot determine block device for DATADIR_BASE: $DATADIR_BASE (got: ${SRC_DEV:-none})" >&2
    exit 1
fi
# Walk up from the filesystem source (partition/LVM/etc.) to the physical disk
NVME_DISK=$(lsblk -srno NAME "$SRC_DEV" 2>/dev/null | tail -n1)
if [[ "$NVME_DISK" != nvme* ]]; then
    echo "ERROR: DATADIR_BASE is not on NVMe storage: $DATADIR_BASE" >&2
    echo "       Filesystem device: $SRC_DEV (physical disk: ${NVME_DISK:-unknown})" >&2
    exit 1
fi

# Pin CPU frequency for stable benchmark results
sudo cpupower frequency-set -g performance -d "${CPU_FREQ_MHZ}MHz" -u "${CPU_FREQ_MHZ}MHz" > /dev/null

echo "============= Running benchmarks for ${DBMS_NAME}:${DBMS_VER} ============="
echo "Server dir:  $SERVER_DIR"
echo "Datadir:     $DATADIR_BASE (NVMe disk: $NVME_DISK)"
echo "CPU freq:    ${CPU_FREQ_MHZ} MHz"
echo "Thread pool: sweep over ${TP_CONFIGS[*]} (size x oversubscribe)"

MYSQLD="${SERVER_DIR}/bin/mysqld"
MYSQL_CLIENT="${SERVER_DIR}/bin/mysql"
MYSQLADMIN="${SERVER_DIR}/bin/${ADMIN_TOOL}"

if [ ! -x "$MYSQLD" ]; then
    echo "ERROR: mysqld not found or not executable: $MYSQLD"
    exit 1
fi

CONFIG_DIR="$HOME/configs"
CONFIG_NAME="my.cnf"
CONFIG_PATH="${CONFIG_DIR}/${CONFIG_NAME}"

# PID file for server management
PID_FILE="/tmp/mysql_benchmark.pid"

server_wait() {
  echo "Waiting for DB Server to initialize..."
  sleep 5

  # Check if mysqld process is running
  if [ -f "$PID_FILE" ]; then
    local pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Fatal error: mysqld process is not running (PID: $pid). Terminating script."
      exit 1
    fi
  else
    echo "Fatal error: PID file not found. Terminating script."
    exit 1
  fi

  until "$MYSQLADMIN" ping --host=$DB_HOST --port=$DB_PORT -u"$DB_USER" -p"$DB_PASS" 2>/dev/null; do
    echo "Waiting for server to respond..."
    sleep 2
  done
  echo "Server is ready!"
}

stop_server() {
  echo "Stopping MySQL server..."
  if [ -f "$PID_FILE" ]; then
    local pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      "$MYSQLADMIN" --host=$DB_HOST --port=$DB_PORT -u"$DB_USER" -p"$DB_PASS" shutdown 2>/dev/null
      sleep 3
      # Force kill if still running
      if kill -0 "$pid" 2>/dev/null; then
        echo "Force killing mysqld (PID: $pid)"
        kill -9 "$pid" 2>/dev/null
        # Wait until the process is actually gone
        while kill -0 "$pid" 2>/dev/null; do
          echo "Waiting for mysqld (PID: $pid) to terminate..."
          sleep 1
        done
      fi
    fi
    rm -f "$PID_FILE"
  fi
  sleep 2
}

start_server() {
  local DATADIR=$1
  local CONFIG=$2

  echo "Starting MySQL server..."
  echo "  Server: $MYSQLD"
  echo "  Datadir: $DATADIR"
  echo "  Config: $CONFIG"
  echo "  Command: $MYSQLD --defaults-file=$CONFIG --datadir=$DATADIR --pid-file=$PID_FILE --user=$(whoami)"

  # Start mysqld in background
  "$MYSQLD" --defaults-file="$CONFIG" --datadir="$DATADIR" --pid-file="$PID_FILE" \
    --user=$(whoami) &
  local MYSQLD_BG_PID=$!

  # Probe for the PID file for up to 3 minutes
  local PID_WAIT_TIMEOUT=180
  local WAITED=0
  while [ ! -f "$PID_FILE" ]; do
    if ! kill -0 "$MYSQLD_BG_PID" 2>/dev/null; then
      echo "ERROR: mysqld exited before creating the PID file"
      exit 1
    fi
    if [ "$WAITED" -ge "$PID_WAIT_TIMEOUT" ]; then
      echo "ERROR: Failed to start mysqld (PID file not created within ${PID_WAIT_TIMEOUT}s)"
      exit 1
    fi
    sleep 2
    WAITED=$(( WAITED + 2 ))
    echo "Waiting for PID file... (${WAITED}s/${PID_WAIT_TIMEOUT}s)"
  done

  echo "mysqld started with PID: $(cat "$PID_FILE")"
}

initialize_datadir() {
  local DATADIR=$1

  echo "Initializing clean data directory: $DATADIR"

  # Remove old datadir if exists
  if [ -d "$DATADIR" ]; then
    echo "Removing old datadir..."
    rm -rf "$DATADIR"
  fi

  # Create fresh datadir
  mkdir -p "$DATADIR"

  # Initialize MySQL data directory
  echo "Running mysqld --initialize-insecure..."
  "$MYSQLD" --initialize-insecure --datadir="$DATADIR" --user=$(whoami)

  if [ $? -ne 0 ]; then
    echo "ERROR: Failed to initialize data directory"
    exit 1
  fi

  echo "Data directory initialized successfully"
}

# Make sure no server is running at this stage
stop_server

# --- DETECT VERSION & VENDOR ---
echo "Starting server to detect version..."

BENCH_DIR="./benchmark_logs"

echo "Removing old config if exists: $CONFIG_PATH"
rm -rf "$CONFIG_PATH"

# Create temporary minimal config for version detection
TMP_DATADIR="${DATADIR_BASE}/tmp_init"
initialize_datadir "$TMP_DATADIR"

# Create minimal config
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_PATH" << EOF
[mysqld]
port=$DB_PORT
socket=/tmp/mysql_benchmark.sock
datadir=$TMP_DATADIR
EOF

start_server "$TMP_DATADIR" "$CONFIG_PATH"
server_wait

# Set root password and grant TCP/IP access (use socket for initial connection)
"$MYSQLADMIN" --socket=/tmp/mysql_benchmark.sock -u"$DB_USER" password "$DB_PASS" 2>/dev/null

# Grant access from 127.0.0.1
"$MYSQL_CLIENT" --socket=/tmp/mysql_benchmark.sock -u"$DB_USER" -p"$DB_PASS" -e "CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '$DB_PASS'; GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION; FLUSH PRIVILEGES;" 2>/dev/null

RAW_VERSION=$("$MYSQL_CLIENT" -h $DB_HOST --port=$DB_PORT -u $DB_USER -p$DB_PASS -N -e "SELECT VERSION();" 2>/dev/null)
MAJOR_VER=$(echo $RAW_VERSION | cut -d'.' -f1,2)

LOG_DIR="${BENCH_DIR}/${DBMS_NAME}/${RAW_VERSION}"
mkdir -p "$LOG_DIR"

echo "Detected: $RAW_VERSION (Major: $MAJOR_VER)"

stop_server
rm -rf "$TMP_DATADIR"

check_innodb_buffer() {
    local EXPECTED_GB=$1
    echo ">>> Verifying InnoDB Buffer Pool: ${EXPECTED_GB}GB..."

    local ACTUAL_BYTES=$("$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -N -s -e "SELECT @@innodb_buffer_pool_size;")
    local ACTUAL_GB=$(( ACTUAL_BYTES / 1024 / 1024 / 1024 ))

    if [ "$ACTUAL_GB" -ne "$EXPECTED_GB" ]; then
        echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo "CRITICAL ERROR: Buffer Pool is ${ACTUAL_GB}GB (Expected ${EXPECTED_GB}GB)"
        echo "Aborting entire benchmark script immediately."
        echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        exit 1
    fi

    echo "Verification successful: Buffer Pool is ${ACTUAL_GB}GB."
}

check_vars_status() {
    local FILE_PREFIX=$1
    echo ">>> Capturing server variables and status..."

    "$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -N -e "SHOW VARIABLES;" > "${FILE_PREFIX}.vars.txt" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "    Variables saved to: ${FILE_PREFIX}.vars.txt"
    else
        echo "    ERROR: Failed to capture variables"
    fi

    "$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -N -e "SHOW STATUS;" > "${FILE_PREFIX}.status.txt" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "    Status saved to: ${FILE_PREFIX}.status.txt"
    else
        echo "    ERROR: Failed to capture status"
    fi
}

run_mysql_summary() {
    local FILE_PREFIX=$1
    ./pt-mysql-summary --host="$DB_HOST" --port=$DB_PORT --user="$DB_USER" --password="$DB_PASS" > "${FILE_PREFIX}-pt-mysql-summary.txt"
    if [ $? -eq 0 ]; then
        echo "    Server summary saved to: ${FILE_PREFIX}-pt-mysql-summary.txt"
    else
        echo "    ERROR: Failed to server summary with pt-mysql-summary"
    fi
}

# --- CONFIGURATION GENERATOR ---
generate_config() {
    local SIZE=$1
    local DATADIR=$2
    local TP_CONF=$3     # "off" or "<thread_pool_size>x<thread_pool_oversubscribe>"
    local CFG="/tmp/$CONFIG_NAME"
    rm -f "$CFG"

    # 1. Start Base Config
    echo "[mysqld]" > "$CFG"
    echo "port                            = $DB_PORT" >> "$CFG"
    echo "socket                          = /tmp/mysql_benchmark.sock" >> "$CFG"
    echo "datadir                         = $DATADIR" >> "$CFG"
    echo "log_error_verbosity             = 3" >> "$CFG"
    echo "log_error                       = ${DATADIR}/mysql-error.log" >> "$CFG"

    echo "# --- General -------------------------------------------------------------------" >> "$CFG"
    echo "user                            = $(whoami)" >> "$CFG"
    echo "bind-address                    = 0.0.0.0" >> "$CFG"
    echo "skip-name-resolve               = ON" >> "$CFG"
    #echo "performance_schema              = OFF" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- Connection & Threading ----------------------------------------------------" >> "$CFG"
    echo "max_connections                 = 2000" >> "$CFG"
    echo "max_connect_errors              = 1000000" >> "$CFG"
    echo "max_prepared_stmt_count         = 1000000" >> "$CFG"
    echo "thread_stack                    = 512K" >> "$CFG"
    echo "thread_cache_size               = 256" >> "$CFG"
    echo "back_log                        = 4096" >> "$CFG"
    echo "wait_timeout                    = 300" >> "$CFG"
    echo "interactive_timeout             = 300" >> "$CFG"
    echo "connect_timeout                 = 60" >> "$CFG"
    echo "" >> "$CFG"

    if [ "$TP_CONF" == "off" ]; then
        echo "# --- Thread Pool DISABLED --------------------------------------------------" >> "$CFG"
        echo "thread_handling                 = one-thread-per-connection" >> "$CFG"
        echo "" >> "$CFG"
    else
        local TP_SIZE="${TP_CONF%x*}"
        local TP_OVERSUB="${TP_CONF#*x}"
        if [[ "${DBMS_NAME,,}" == percona* ]]; then
            echo "# --- Thread Pool (Percona Server) ------------------------------------------" >> "$CFG"
            echo "thread_handling                 = pool-of-threads" >> "$CFG"
            echo "thread_pool_size                = $TP_SIZE" >> "$CFG"
            echo "thread_pool_max_threads         = 2000" >> "$CFG"
            echo "thread_pool_oversubscribe       = $TP_OVERSUB" >> "$CFG"
        else
            echo "# --- Thread Pool (MySQL 26.7.0+ plugin) -------------------------------------" >> "$CFG"
            echo "#thread_handling                = pool-of-threads   # Percona Server only" >> "$CFG"
            echo "plugin-load-add                 = thread_pool.so" >> "$CFG"
            echo "thread_pool_size                = $TP_SIZE" >> "$CFG"
            echo "thread_pool_max_active_query_threads = 2000        # ~ Percona thread_pool_max_threads" >> "$CFG"
            echo "thread_pool_query_threads_per_group  = $(( TP_OVERSUB + 1 ))  # ~ Percona thread_pool_oversubscribe=$TP_OVERSUB" >> "$CFG"
        fi
        echo "" >> "$CFG"
    fi

    echo "# --- InnoDB - Buffer pool Tier -------------------------------------------------" >> "$CFG"
    echo "innodb_buffer_pool_size         = ${SIZE}G" >> "$CFG"
    echo "innodb_buffer_pool_load_at_startup  = OFF" >> "$CFG"
    echo "innodb_buffer_pool_dump_at_shutdown = OFF" >> "$CFG"

    echo "" >> "$CFG"
    echo "# --- InnoDB – I/O (NVMe can saturate many threads) ----------------------------" >> "$CFG"
    echo "innodb_io_capacity              = 10000" >> "$CFG"
    echo "innodb_io_capacity_max          = 20000" >> "$CFG"
    echo "innodb_read_io_threads          = 16" >> "$CFG"
    echo "innodb_write_io_threads         = 16" >> "$CFG"
    echo "innodb_use_native_aio           = ON" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- InnoDB – Log / Durability -------------------------------------------------" >> "$CFG"
    echo "innodb_log_buffer_size          = 256M" >> "$CFG"
    echo "innodb_flush_log_at_trx_commit  = 1          # full ACID; use 2 for ~10 % more speed" >> "$CFG"
    echo "innodb_doublewrite              = ON" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- InnoDB – Concurrency & OLTP Tuning ---------------------------------------" >> "$CFG"
    echo "innodb_stats_on_metadata        = OFF" >> "$CFG"
    echo "innodb_open_files               = 65536" >> "$CFG"
    echo "innodb_lock_wait_timeout        = 50" >> "$CFG"
    echo "innodb_rollback_on_timeout      = ON" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- Per-Session Buffers (keep modest; many connections × this = RAM) ----------" >> "$CFG"
    echo "sort_buffer_size                = 4M" >> "$CFG"
    echo "join_buffer_size                = 4M" >> "$CFG"
    echo "read_buffer_size                = 2M" >> "$CFG"
    echo "read_rnd_buffer_size            = 4M" >> "$CFG"
    echo "tmp_table_size                  = 256M" >> "$CFG"
    echo "max_heap_table_size             = 256M" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- Table & File Handles ------------------------------------------------------" >> "$CFG"
    echo "table_open_cache                = 65536" >> "$CFG"
    echo "table_definition_cache          = 65536" >> "$CFG"
    echo "open_files_limit                = 1000000" >> "$CFG"
    echo "table_open_cache_instances      = 64" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- Binary Log ----------------------------------------------------------------" >> "$CFG"
    echo "# Binary logging DISABLED for benchmarking" >> "$CFG"
    echo "disable_log_bin                 = ON" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- Slow Query Log ------------------------------------------------------------" >> "$CFG"
    echo "slow_query_log                  = ON" >> "$CFG"
    echo "slow_query_log_file             = ${DATADIR}/slow.log" >> "$CFG"
    echo "long_query_time                 = 1" >> "$CFG"
    echo "log_queries_not_using_indexes   = OFF" >> "$CFG"
    echo "min_examined_row_limit          = 1000" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- Character Set -------------------------------------------------------------" >> "$CFG"
    echo "character_set_server            = utf8mb4" >> "$CFG"
    echo "collation_server                = utf8mb4_unicode_ci" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- Misc ----------------------------------------------------------------------" >> "$CFG"
    echo "max_allowed_packet              = 64M" >> "$CFG"
    echo "bulk_insert_buffer_size         = 256M" >> "$CFG"
    echo "myisam_sort_buffer_size         = 128M" >> "$CFG"
    echo "key_buffer_size                 = 64M        # MyISAM only; keep small for OLTP" >> "$CFG"
    echo "" >> "$CFG"

    echo "# --- Version specific settings -------------------------------------------------" >> "$CFG"

    # 3. VERSION SPECIFIC LOGIC
    INSTANCES=$(( SIZE / 5 ))
    [ "$INSTANCES" -lt 1 ] && INSTANCES=1
    [ "$INSTANCES" -gt 8 ] && INSTANCES=8

    # MySQL 8.4+ / 9.x
    echo "innodb_redo_log_capacity = 4G" >> "$CFG"
    echo "innodb_change_buffering = none" >> "$CFG"
    echo "innodb_flush_method = O_DIRECT" >> "$CFG"
    echo "innodb_buffer_pool_instances    = $INSTANCES" >> "$CFG"

    # Percona Server specific settings
    # if [[ "$DBMS_NAME" == "percona-server" ]]; then
    #     echo "innodb_empty_free_list_algorithm = backoff" >> "$CFG"
    # fi

    # 4. Deploy Config
    mkdir -p "$CONFIG_DIR"
    cp "$CFG" "$CONFIG_PATH"
    cp "$CFG" "${LOG_DIR}/Tier${SIZE}G_$(tp_label "$TP_CONF").cnf.txt"

    chmod 644 "$CONFIG_PATH"
}

copy_server_logs() {
    local SIZE=$1
    local DATADIR=$2
    local DEST_DIR="${LOG_DIR}"

    echo "Copying server logs to ${DEST_DIR}..."
    if [ -f "${DATADIR}/mysql-error.log" ]; then
        cp "${DATADIR}/mysql-error.log" "${DEST_DIR}/Tier${SIZE}G.errlog.txt"
    fi
}

# --- TELEMETRY FUNCTIONS ---
start_innodb_metrics() {
    local PREFIX=$1
    local OUT="${PREFIX}.innodb.txt"
    echo "innodb metrics -> ${OUT}"

    (
        # Header: one column per metric NAME, sorted
        HEADER=$("$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -N -B \
            -e "SELECT NAME FROM information_schema.INNODB_METRICS ORDER BY NAME" 2>/dev/null \
            | paste -sd,)
        echo "timestamp,${HEADER}" > "$OUT"

        while :; do
            TS=$(date +%s.%3N)
            VALS=$("$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -N -B \
                -e "SELECT COUNT FROM information_schema.INNODB_METRICS ORDER BY NAME" 2>/dev/null \
                | paste -sd,)
            echo "${TS},${VALS}" >> "$OUT"
            sleep 1
        done
    ) &
    echo $! > /tmp/innodb.pid
}

start_lru_metrics() {
    local PREFIX=$1
    local OUT="${PREFIX}.lru_metrics.csv"
    echo "all enabled InnoDB metrics (long format) -> ${OUT}"

    # Get the directory of this script
    local SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local COLLECTOR="${SCRIPT_DIR}/collect_lru_metrics.sh"

    if [ ! -x "$COLLECTOR" ]; then
        echo "WARNING: InnoDB metrics collector not found or not executable: $COLLECTOR"
        return 1
    fi

    # Start the collector in the background
    "$COLLECTOR" "$DB_HOST" "$DB_PORT" "$DB_USER" "$DB_PASS" "$OUT" 2>/dev/null &
    local pid=$!
    echo $pid > /tmp/lru_metrics.pid

    # Verify it started successfully
    sleep 0.5
    if ! kill -0 $pid 2>/dev/null; then
        echo "WARNING: Failed to start InnoDB metrics collector"
        rm -f /tmp/lru_metrics.pid
        return 1
    fi

    return 0
}

start_mutex_metrics() {
    local PREFIX=$1
    local OUT="${PREFIX}.mutex_metrics.csv"
    echo "InnoDB mutex metrics -> ${OUT}"

    # Get the directory of this script
    local SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local COLLECTOR="${SCRIPT_DIR}/collect_mutex_metrics.sh"

    if [ ! -x "$COLLECTOR" ]; then
        echo "WARNING: Mutex metrics collector not found or not executable: $COLLECTOR"
        return 1
    fi

    # Start the collector in the background
    "$COLLECTOR" "$DB_HOST" "$DB_PORT" "$DB_USER" "$DB_PASS" "$OUT" 2>/dev/null &
    local pid=$!
    echo $pid > /tmp/mutex_metrics.pid

    # Verify it started successfully
    sleep 0.5
    if ! kill -0 $pid 2>/dev/null; then
        echo "WARNING: Failed to start mutex metrics collector"
        rm -f /tmp/mutex_metrics.pid
        return 1
    fi

    return 0
}

enable_innodb_metrics() {
    echo ">>> Enabling all InnoDB metrics counters..."
    "$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -N \
        -e "SET GLOBAL innodb_monitor_enable = 'latch';" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "    innodb_monitor_enable = 'latch'"
    else
        echo "    ERROR: Failed to set innodb_monitor_enable"
    fi

    # Note: 'all' enables all available metrics including buffer_LRU_% if present
}

start_gdb_snapshots() {
    local PREFIX=$1
    local OUT="${PREFIX}.pt-pmp.txt"
    local DELAY=$((DURATION / 2))

    echo "pt-pmp stack profiling -> ${OUT} (will start after ${DELAY}s)"

    (
        # Wait for half of benchmark duration before starting profiling
        echo "Waiting ${DELAY} seconds before starting pt-pmp profiling..." > "$OUT"
        sleep $DELAY

        echo "" >> "$OUT"
        echo "Starting stack trace collection at $(date)" >> "$OUT"
        echo "Collecting stack traces using pt-pmp (auto-detecting mysqld)" >> "$OUT"
        echo "================================================" >> "$OUT"
        echo "" >> "$OUT"

        # Get absolute path to current directory for pt-eustack-resolver
        local SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

        # Run pt-pmp with sudo, providing PATH so it can find pt-eustack-resolver
        # Collect 30 snapshots with pt-pmp (auto-detects mysqld process)
        sudo env "PATH=$SCRIPT_DIR:$PATH" "$SCRIPT_DIR/pt-pmp" -i 30 -d pteu >> "$OUT" 2>&1

        echo "" >> "$OUT"
        echo "Profiling completed at $(date)" >> "$OUT"
    ) &
    echo $! > /tmp/gdb.pid
}

start_thread_status() {
    local PREFIX=$1
    local OUT_THPOOL="${PREFIX}.stat-thpool.txt"
    local OUT_THR="${PREFIX}.stat-thr.txt"
    echo "Thread pool status -> ${OUT_THPOOL}"
    echo "Threads status -> ${OUT_THR}"

    (
        while :; do
            TS=$(date +%s.%3N)
            "$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -N -e "SHOW GLOBAL STATUS LIKE 'Threadpool%';" 2>/dev/null | awk -v ts="$TS" '{print ts"\t"$0}' >> "$OUT_THPOOL"
            sleep 1
        done
    ) &
    echo $! > /tmp/thread_status_thpool.pid

    (
        while :; do
            TS=$(date +%s.%3N)
            "$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -N -e "SHOW GLOBAL STATUS LIKE 'Threads%';" 2>/dev/null | awk -v ts="$TS" '{print ts"\t"$0}' >> "$OUT_THR"
            sleep 1
        done
    ) &
    echo $! > /tmp/thread_status_thr.pid
}

start_metrics() {
    local PREFIX=$1
    echo " --- START METRICS ---"

    iostat -dxm 1 > "${PREFIX}.iostat.txt" & echo $! > /tmp/iostat.pid
    vmstat 1 > "${PREFIX}.vmstat.txt" & echo $! > /tmp/vmstat.pid
    mpstat -P ALL 1 > "${PREFIX}.mpstat.txt" & echo $! > /tmp/mpstat.pid
    dstat -t 1 > "${PREFIX}.dstat.txt" & echo $! > /tmp/dstat.pid

    start_innodb_metrics "$PREFIX"
    start_lru_metrics "$PREFIX"
    start_mutex_metrics "$PREFIX"
    start_gdb_snapshots "$PREFIX"
    start_thread_status "$PREFIX"
}

stop_metrics() {
    # Stop all monitoring processes
    local pids_to_kill=""

    for pidfile in /tmp/iostat.pid /tmp/vmstat.pid /tmp/mpstat.pid /tmp/dstat.pid /tmp/innodb.pid /tmp/lru_metrics.pid /tmp/mutex_metrics.pid /tmp/gdb.pid /tmp/thread_status_thpool.pid /tmp/thread_status_thr.pid; do
        if [ -f "$pidfile" ]; then
            pids_to_kill="$pids_to_kill $(cat $pidfile)"
        fi
    done

    if [ -n "$pids_to_kill" ]; then
        kill $pids_to_kill 2>/dev/null
    fi

    # Give GDB snapshot a moment to finish if still running
    if [ -f /tmp/gdb.pid ]; then
        local gdb_pid=$(cat /tmp/gdb.pid)
        if kill -0 "$gdb_pid" 2>/dev/null; then
            echo "Waiting for GDB snapshots to complete..."
            sleep 2
        fi
    fi

    # Clean up PID files
    rm -f /tmp/iostat.pid /tmp/vmstat.pid /tmp/mpstat.pid /tmp/dstat.pid /tmp/innodb.pid /tmp/lru_metrics.pid /tmp/mutex_metrics.pid /tmp/gdb.pid /tmp/thread_status_thpool.pid /tmp/thread_status_thr.pid
}

trap 'stop_metrics; stop_server' EXIT
trap 'stop_metrics; stop_server; exit 1' INT TERM

init_data() {
  echo ">>> Create tables and insert data..."
  sysbench oltp_read_only --mysql-host=$DB_HOST --mysql-port=$DB_PORT --mysql-user=$DB_USER --mysql-password=$DB_PASS \
    --mysql-db=$DB_DATABASE --tables=20 --table-size=$TABLE_ROWS --threads=64 prepare
}

# --- EXECUTION LOOP ---
for SIZE in "${POOL_SIZES[@]}"; do
  echo "========================================================="
  echo ">>> TIER: ${SIZE}GB | VER: $RAW_VERSION <<<"
  echo "========================================================="

  # 1. Create clean datadir for this tier (shared across thread pool configs)
  TIER_DATADIR="${DATADIR_BASE}/${DBMS_NAME}_${RAW_VERSION}_tier${SIZE}G"

  initialize_datadir "$TIER_DATADIR"
  TIER_DATA_LOADED="0"

  # 2. Thread pool sweep: restart the server with a new config for each entry
  for TP_CONF in "${TP_CONFIGS[@]}"; do
    TP_LABEL=$(tp_label "$TP_CONF")
    echo "---------------------------------------------------------"
    echo ">>> TIER: ${SIZE}GB | THREAD POOL: ${TP_CONF} <<<"
    echo "---------------------------------------------------------"

    generate_config $SIZE "$TIER_DATADIR" "$TP_CONF"

    echo "Starting server with the new config..."
    start_server "$TIER_DATADIR" "$CONFIG_PATH"
    server_wait

    if [ "$TIER_DATA_LOADED" != "1" ]; then
      # Set root password and grant TCP/IP access (use socket for initial connection after fresh init)
      "$MYSQLADMIN" --socket=/tmp/mysql_benchmark.sock -u"$DB_USER" password "$DB_PASS" 2>/dev/null

      # Grant access from 127.0.0.1
      "$MYSQL_CLIENT" --socket=/tmp/mysql_benchmark.sock -u"$DB_USER" -p"$DB_PASS" -e "CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '$DB_PASS'; GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION; FLUSH PRIVILEGES;" 2>/dev/null

      # Create database
      "$MYSQL_CLIENT" -h "$DB_HOST" --port=$DB_PORT -u "$DB_USER" -p"$DB_PASS" -e "CREATE DATABASE IF NOT EXISTS ${DB_DATABASE};" 2>/dev/null
    fi

    echo "Server started with custom config."
    check_innodb_buffer $SIZE
    enable_innodb_metrics
    check_vars_status "${LOG_DIR}/Tier${SIZE}G_${TP_LABEL}"

    if [ "$TIER_DATA_LOADED" != "1" ]; then
      init_data
      TIER_DATA_LOADED="1"
    fi

    run_mysql_summary "${LOG_DIR}/Tier${SIZE}G_${TP_LABEL}"

    # 3. WARMUP
    echo ">>> Warmup: Dirty Writes (${WARMUP_TIME}s)..."
    sysbench oltp_read_write --mysql-host=$DB_HOST --mysql-port=$DB_PORT --mysql-user=$DB_USER --mysql-password=$DB_PASS \
        --mysql-db=$DB_DATABASE --tables=20 --table-size=$TABLE_ROWS --threads=64 --time=$WARMUP_TIME run
    TEST_TYPE="oltp_read_write"

    # 4. MEASUREMENT (NUM_RUNS runs per thread count for stability)
    RUN_END=$(( RUN_START + NUM_RUNS - 1 ))
    for THREAD in "${THREADS[@]}"; do
      for (( RUN=RUN_START; RUN<=RUN_END; RUN++ )); do
        FILE_PREFIX="${LOG_DIR}/run${RUN}_${TABLE_ROWS_LABEL}_Tier${SIZE}G_${TP_LABEL}_RW_${THREAD}th"
        echo "   >>> Testing ${THREAD} Threads (run ${RUN} of ${RUN_START}..${RUN_END})..."

        start_metrics "$FILE_PREFIX"

        sysbench $TEST_TYPE \
          --mysql-host=$DB_HOST \
          --mysql-port=$DB_PORT \
          --mysql-user=$DB_USER \
          --mysql-password=$DB_PASS \
          --mysql-db=$DB_DATABASE \
          --tables=20 \
          --table-size=$TABLE_ROWS \
          --threads=$THREAD \
          --time=$DURATION \
          --report-interval=1 \
          --rand-type=uniform \
          --mysql-ssl=off \
          run > "${FILE_PREFIX}.sysbench.txt"

        stop_metrics
        sleep 10
      done
    done

    stop_server
  done

  copy_server_logs $SIZE "$TIER_DATADIR"
done

echo "============= Finished benchmarks for ${DBMS_NAME}:${DBMS_VER} ============="
