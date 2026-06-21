#!/usr/bin/env bash
# FIRE Capital - MMR Summary Runner
# Usage: ./run_summary.sh "ERA_MMR_-_06_15_26.xlsx"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "$1" ]; then
    echo "Usage: $0 <filename>"
    echo "Example: $0 \"ERA_MMR_-_06_15_26.xlsx\""
    exit 1
fi

FILE="$1"

# If not a full path, look next to this script
if [ ! -f "$FILE" ]; then
    FILE="$SCRIPT_DIR/$1"
fi

if [ ! -f "$FILE" ]; then
    echo "Error: File not found: $1"
    exit 1
fi

python3 "$SCRIPT_DIR/generate_summary.py" "$FILE"
