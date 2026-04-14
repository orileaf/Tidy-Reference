#!/bin/bash
# scripts/run_llm.sh — LLM structured extraction: data/01_raw/refs_raw.json → data/02_llm/llm_results.json
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/config.env" ]]; then
    set -a; source "$PROJECT_ROOT/config.env"; set +a
fi

PYTHON="${PIPTHON:-python3}"

cd "$PROJECT_ROOT"
$PYTHON -m src.modules.llm_parse
