#!/bin/bash
# scripts/run_export.sh — Export bibliography: data/04_quality/qa_approved.json
#   → data/05_export/references.bib + data/05_export/references_gb.txt
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/config.env" ]]; then
    set -a; source "$PROJECT_ROOT/config.env"; set +a
fi

PYTHON="${PIPTHON:-python3}"
FORMAT="${1:-}"

cd "$PROJECT_ROOT"
if [[ -n "$FORMAT" ]]; then
    $PYTHON -m src.modules.export --format "$FORMAT"
else
    $PYTHON -m src.modules.export
fi
