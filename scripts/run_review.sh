#!/bin/bash
# scripts/run_review.sh — QA judgment + interactive review workflow
#
# Usage:
#   ./run_review.sh                    run QA judgment (only — safe to re-run)
#   ./run_review.sh --review           interactive review (resumable)
#   ./run_review.sh --approve          merge approved entries → bib_export_report.md
#   ./run_review.sh --manual           manual search: parse research_text → LLM → update qa_review.json
#
# Full workflow:
#   ./run_review.sh                   QA classification → qa_review.json
#   ./run_review.sh --review          interactive review
#                                    (auto-run --approve + reset manual_research.json when done)
#   # fill in manual_research.json research_text for skipped entries
#   ./run_review.sh --manual          parse research_text → LLM → QA → update qa_review.json
#   ./run_review.sh --review          review medium/low entries with manual_data
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
    --manual)
        $PYTHON -m src.modules.quality --manual
        ;;
    *)
        $PYTHON -m src.modules.quality
        ;;
esac
