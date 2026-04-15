#!/bin/bash
# scripts/run_all.sh — Run the complete reference processing pipeline.
# Pauses after QA judgment so you can run interactive review before merge.
#
# Full workflow:
#   ./run_all.sh                  parse → llm → search → QA judgment (pauses)
#   ./run_review.sh --review      interactive review
#                                    (auto: --approve + reset manual_research.json when done)
#   # fill in manual_research.json for skipped entries, then:
#   ./run_review.sh --manual      parse research_text → LLM → QA → update qa_review.json
#   ./run_review.sh --review      review remaining medium/low entries
#   ./run_export.sh              export bibliography
#
# Or use the skill CLI directly:
#   python -m src.skill run
#   python -m src.skill review --review
#   python -m src.skill review --manual
#   python -m src.skill export

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load local config (config.env is gitignored)
if [[ -f "$PROJECT_ROOT/config.env" ]]; then
    set -a; source "$PROJECT_ROOT/config.env"; set +a
fi

PYTHON="${PIPTHON:-python3}"
INPUT="${1:-1.docx}"

cd "$PROJECT_ROOT"

echo ""
echo "═══════════════════════════════════════════════════════"
echo " Reference Processing Pipeline"
echo "═══════════════════════════════════════════════════════"
echo ""

$PYTHON -m src.skill run "$INPUT"

echo ""
echo "═══════════════════════════════════════════════════════"
echo " Pipeline paused — QA judgment complete."
echo ""
echo " Next steps:"
echo "   ./run_review.sh --review   # interactive review"
echo "   # fill in manual_research.json for skipped entries"
echo "   ./run_review.sh --manual  # parse research_text → LLM → QA"
echo "   ./run_export.sh           # export bibliography"
echo "═══════════════════════════════════════════════════════"
