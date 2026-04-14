#!/usr/bin/env python3
"""
src/skill.py — Skill CLI entry point for the reference management tool.

Usage:
  python -m src.skill <command>

Commands:
  parse     Parse a document → data/01_raw/refs_raw.json
  llm       LLM structured extraction → data/02_llm/llm_results.json
  search    Run the search cascade → data/03_search/search_results.json
  review    Run QA judgment → data/04_quality/qa_results.json + qa_review.json
  review --review   Interactive review (resumable; run after 'review')
  review --approve  Merge approved entries → qa_approved.json + bib_export_report.md
  export    Export bibliography → data/05_export/references.bib + references_gb.txt
  run       Run the full pipeline up to QA judgment (pause for interactive review)

Workflow:
  python -m src.skill run
    → parse → llm → search → review (QA only)
    → INTERACTIVE REVIEW HERE ←
  python -m src.skill review --approve   # merge approved entries
  python -m src.skill export            # export bibliography

Resume interrupted review:
  python -m src.skill review --review    # re-enter review from where you left off

Examples:
  python -m src.skill parse                    # uses project_root/1.docx
  python -m src.skill parse data/my_refs.txt  # custom input file
  python -m src.skill run                      # parse → export (pauses for review)
  python -m src.skill run --no-mcp           # skip MCP fallback
  python -m src.skill search --no-mcp        # skip MCP fallback
  python -m src.skill export --format bib     # BibTeX only
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def _run_module(module: str, extra_args: list[str] | None = None):
    cmd = [sys.executable, "-m", f"src.modules.{module}"]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        sys.exit(result.returncode)


def cmd_parse(args):
    extra = [args.input_file] if args.input_file else []
    _run_module("parser", extra)


def cmd_llm(args):
    _run_module("llm_parse")


def cmd_search(args):
    extra = ["--no-mcp"] if args.no_mcp else []
    _run_module("search", extra)


def cmd_review(args):
    extra = []
    if args.approve:
        extra.append("--approve")
    elif args.review:
        extra.append("--review")
    _run_module("quality", extra)


def cmd_export(args):
    extra = ["--format", args.format] if args.format else []
    _run_module("export", extra)


def cmd_run(args):
    print("══ Skill: Full Pipeline ══\n")

    print("Step 1: Parse document → data/01_raw/refs_raw.json")
    input_file = args.input or "1.docx"
    _run_module("parser", [input_file])

    print("\nStep 2: LLM structured extraction → data/02_llm/llm_results.json")
    _run_module("llm_parse")

    print("\nStep 3: Search cascade → data/03_search/search_results.json")
    extra = ["--no-mcp"] if args.no_mcp else []
    _run_module("search", extra)

    print("\nStep 4: LLM QA judgment → data/04_quality/qa_results.json + qa_review.json")
    _run_module("quality")

    print("\n  ⏸ PAUSE — run interactive review now:")
    print("    python -m src.skill review --review")
    print("  After review is done, run:")
    print("    python -m src.skill review --approve")
    print("  Then continue export:")
    print("    python -m src.skill export")
    return

    print("\n══ Pipeline complete ══")


def main():
    parser = argparse.ArgumentParser(
        prog="python -m src.skill",
        description="Reference management skill CLI",
    )
    sub = parser.add_subparsers(required=True)

    # ── parse ──────────────────────────────────────────────────────────────────
    p_parse = sub.add_parser("parse", help="Parse document → refs_raw.json")
    p_parse.add_argument("input_file", nargs="?", help="Input .docx or .txt file")
    p_parse.set_defaults(func=cmd_parse)

    # ── llm ───────────────────────────────────────────────────────────────────
    p_llm = sub.add_parser("llm", help="LLM structured extraction → llm_results.json")
    p_llm.set_defaults(func=cmd_llm)

    # ── search ────────────────────────────────────────────────────────────────
    p_search = sub.add_parser("search", help="Search cascade → search_results.json")
    p_search.add_argument("--no-mcp", action="store_true",
                           help="Skip MCP fallback step")
    p_search.set_defaults(func=cmd_search)

    # ── review ────────────────────────────────────────────────────────────────
    p_review = sub.add_parser("review", help="LLM QA judgment + approval")
    p_review.add_argument("--approve", action="store_true",
                          help="Merge approved entries → qa_approved.json")
    p_review.add_argument("--review", action="store_true",
                          help="Interactive review (resumable; run after 'review' without flags)")
    p_review.set_defaults(func=cmd_review)

    # ── export ────────────────────────────────────────────────────────────────
    p_export = sub.add_parser("export", help="Export bibliography")
    p_export.add_argument("--format",
                          choices=["bib", "gb", "bib,gb"],
                          help="Export format: 'bib', 'gb', or 'bib,gb' (default: both)")
    p_export.set_defaults(func=cmd_export)

    # ── run ────────────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Run full pipeline")
    p_run.add_argument("input", nargs="?", default="1.docx",
                       help="Input file (default: 1.docx)")
    p_run.add_argument("--no-mcp", action="store_true",
                       help="Skip MCP fallback step")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
