#!/bin/bash
# InnoDB Mutex Metrics Collector
# Collects mutex data from SHOW ENGINE INNODB MUTEX every 1 second

if [ $# -ne 5 ]; then
    echo "Usage: $0 <host> <port> <user> <password> <output_file>" >&2
    echo "Example: $0 127.0.0.1 3306 root password mutex_metrics.csv" >&2
    exit 1
fi

DB_HOST="$1"
DB_PORT="$2"
DB_USER="$3"
DB_PASS="$4"
OUTPUT_FILE="$5"

# MySQL client command with credentials
MYSQL_CMD="mysql -h $DB_HOST --port=$DB_PORT -u $DB_USER -p$DB_PASS -N -B"

# Check if MySQL is accessible
if ! $MYSQL_CMD -e "SELECT 1" 2>/dev/null >/dev/null; then
    echo "ERROR: Failed to connect to MySQL server" >&2
    exit 1
fi

# Write CSV header
echo "timestamp_unix,timestamp_human,type,name,spins,waits,calls" > "$OUTPUT_FILE"

echo "Recording InnoDB mutex metrics to $OUTPUT_FILE" >&2
echo "Sampling every 1 second. Press Ctrl+C to stop." >&2

# Collection loop
while true; do
    # Get current timestamp
    TIMESTAMP_UNIX=$(date +%s.%3N)
    TIMESTAMP_HUMAN=$(date '+%Y-%m-%d %H:%M:%S.%3N')

    # Query for mutex data using SHOW ENGINE INNODB MUTEX
    # The output format varies, but typically includes:
    # Type | Name | Status
    # We need to parse the output which can be in different formats

    $MYSQL_CMD -e "SHOW ENGINE INNODB MUTEX" 2>/dev/null | while IFS=$'\t' read -r type name status; do
        # Skip header row if present
        if [ "$type" = "Type" ] || [ -z "$type" ]; then
            continue
        fi

        # Parse the status field which typically contains create_file:line, os_waits=N
        # Example: "os_waits=12345"
        # Example: "sync/sync0sync.cc:123, os_waits=456"

        spins=""
        waits=""
        calls=""

        # Extract spins value
        if [[ "$status" =~ spins=([0-9]+) ]]; then
            spins="${BASH_REMATCH[1]}"
        fi

        # Extract waits value
        if [[ "$status" =~ waits=([0-9]+) ]]; then
            waits="${BASH_REMATCH[1]}"
        fi

        # Extract calls value
        if [[ "$status" =~ calls=([0-9]+) ]]; then
            calls="${BASH_REMATCH[1]}"
        fi

        # Clean up fields (remove quotes and extra spaces)
        type=$(echo "$type" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        name=$(echo "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

        # Write to CSV: timestamp_unix,timestamp_human,type,name,spins,waits,calls
        printf "%s,%s,%s,%s,%s,%s,%s\n" \
            "$TIMESTAMP_UNIX" \
            "$TIMESTAMP_HUMAN" \
            "$type" \
            "$name" \
            "$spins" \
            "$waits" \
            "$calls" >> "$OUTPUT_FILE"
    done

    sleep 1
done
