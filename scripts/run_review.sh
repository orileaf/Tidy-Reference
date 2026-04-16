#!/bin/bash
# scripts/run_review.sh — QA judgment + interactive review workflow
#
# Usage:
#   ./run_review.sh                    run QA judgment (only — safe to re-run)
#   ./run_review.sh --review           interactive review (resumable)
#   ./run_review.sh --approve          merge approved entries → bib_export_report.md
#   ./run_review.sh --manual           manual search: parse research_text → LLM → update qa_review.json
#   ./run_review.sh --manual-review    interactive review of manual_review.json entries
#   ./run_review.sh --manual-approve   merge approved manual entries into qa_approved.json
#
# Full workflow for unresolvable entries:
#   ./run_review.sh --review           # interactive review
#   # fill in manual_research.json research_text for skipped entries
#   ./run_review.sh --manual           # parse research_text → LLM → QA → manual_review.json
#   ./run_review.sh --manual-review     # review medium/low entries with manual_data
#   ./run_review.sh --manual-approve    # merge into qa_approved.json
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
    --manual-review)
        $PYTHON -m src.modules.quality --manual-review
        ;;
    --manual-approve)
        $PYTHON -m src.modules.quality --manual-approve
        ;;
    *)
        $PYTHON -m src.modules.quality
        ;;
esac
