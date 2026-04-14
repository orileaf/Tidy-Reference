#!/bin/bash
# scripts/run_review.sh — QA judgment + interactive review workflow
#
# Usage:
#   ./run_review.sh               run QA judgment (only — safe to re-run)
#   ./run_review.sh --review      interactive review (resumable, enter any time)
#   ./run_review.sh --approve     merge approved entries → bib_export_report.md
#
# Full workflow:
#   ./run_review.sh               # QA classification → qa_review.json
#   ./run_review.sh --review      # interactive review (can exit + re-enter)
#   ./run_review.sh --approve     # merge + generate report
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/config.env" ]]; then
    set -a; source "$PROJECT_ROOT/config.env"; set +a
fi

PYTHON="${PIPTHON:-python3}"

cd "$PROJECT_ROOT"

case "${1:-}" in
    --review)
        $PYTHON -m src.modules.quality --review
        ;;
    --approve)
        $PYTHON -m src.modules.quality --approve
        ;;
    *)
        $PYTHON -m src.modules.quality
        ;;
esac
