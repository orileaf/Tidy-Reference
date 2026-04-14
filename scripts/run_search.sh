#!/bin/bash
# scripts/run_search.sh — Search cascade: data/02_llm/llm_results.json → data/03_search/search_results.json
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/config.env" ]]; then
    set -a; source "$PROJECT_ROOT/config.env"; set +a
fi

PYTHON="${PIPTHON:-python3}"

cd "$PROJECT_ROOT"
if [[ "${1:-}" == "--no-mcp" ]]; then
    $PYTHON -m src.modules.search --no-mcp
else
    $PYTHON -m src.modules.search
fi
