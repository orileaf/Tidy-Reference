#!/bin/bash
# scripts/run_parser.sh — Parse .docx/.txt → data/01_raw/refs_raw.json
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/config.env" ]]; then
    set -a; source "$PROJECT_ROOT/config.env"; set +a
fi

PYTHON="${PIPTHON:-python3}"
INPUT="${1:-1.docx}"

cd "$PROJECT_ROOT"
$PYTHON -m src.modules.parser "$INPUT"
