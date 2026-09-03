#!/bin/bash
# run_pt.sh
# Downloads and sets up Percona Toolkit utilities for benchmarking

echo "=========================================="
echo "Setting up Percona Toolkit utilities"
echo "=========================================="

# Create benchmark_logs directory if it doesn't exist
mkdir -p benchmark_logs

# Download pt-summary if not present
if [ ! -f ./pt-summary ]; then
  echo "Downloading pt-summary..."
  wget -q http://percona.com/get/pt-summary
  chmod +x pt-summary
  echo "  ✓ pt-summary downloaded"
else
  echo "  ✓ pt-summary already exists"
fi

# Download pt-pmp if not present
if [ ! -f ./pt-pmp ]; then
  echo "Downloading pt-pmp..."
  wget -q https://raw.githubusercontent.com/percona/percona-toolkit/3.x/bin/pt-pmp
  chmod +x pt-pmp
  echo "  ✓ pt-pmp downloaded"
else
  echo "  ✓ pt-pmp already exists"
fi

# Download pt-eustack-resolver if not present
if [ ! -f ./pt-eustack-resolver ]; then
  echo "Downloading pt-eustack-resolver..."
  wget -q https://raw.githubusercontent.com/percona/percona-toolkit/3.x/bin/pt-eustack-resolver
  chmod +x pt-eustack-resolver
  echo "  ✓ pt-eustack-resolver downloaded"
else
  echo "  ✓ pt-eustack-resolver already exists"
fi

# Run pt-summary and generate reports
echo ""
echo "Generating pt-summary reports..."
SUMMARY_FULL="benchmark_logs/pt-summary-full.txt"
SUMMARY_BRIEF="benchmark_logs/pt-summary-brief.txt"
./pt-summary > "$SUMMARY_FULL"

if [[ ! -f "$SUMMARY_FULL" ]]; then
  echo "Error: Failed to generate pt-summary report."
  exit 1
fi

extract() {
  local label="$1"
  local field="$2"
  local value
  value=$(grep -m1 "^\s*${field}\s*|" "$SUMMARY_FULL" | sed 's/.*| *//')
  printf "%-20s %s\n" "${label}:" "${value:-N/A}"
}

echo "========================================" > "$SUMMARY_BRIEF"
echo " Percona pt-summary System Info" >> "$SUMMARY_BRIEF"
echo "========================================" >> "$SUMMARY_BRIEF"
extract "Platform"     "Platform" >> "$SUMMARY_BRIEF"
extract "Release"      "Release" >> "$SUMMARY_BRIEF"
extract "Kernel"       "Kernel" >> "$SUMMARY_BRIEF"
extract "Architecture" "Architecture" >> "$SUMMARY_BRIEF"
extract "Processors"   "Processors" >> "$SUMMARY_BRIEF"
extract "Models"       "Models" >> "$SUMMARY_BRIEF"
extract "Memory Total" "Total" >> "$SUMMARY_BRIEF"
echo "========================================" >> "$SUMMARY_BRIEF"

echo "  ✓ Full report: $SUMMARY_FULL"
echo "  ✓ Brief report: $SUMMARY_BRIEF"

echo ""
echo "=========================================="
echo "Percona Toolkit setup complete!"
echo "=========================================="
echo "Available tools:"
echo "  - pt-summary: System information"
echo "  - pt-pmp: Poor man's profiler (stack traces)"
echo "  - pt-eustack-resolver: Resolve eu-stack output"
echo "=========================================="
