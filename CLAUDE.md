# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A bibliography enrichment tool for scientific papers. Extracts references from a `.docx`/`.txt` file, parses them into structured fields with an LLM, cross-verifies via Crossref + Semantic Scholar APIs (with an optional MCP web-search fallback), runs LLM-powered quality assessment, and exports formatted BibTeX and GB/T 7714-2015 bibliographies.

## Setup

```bash
cp config.env.example config.env
# edit config.env — at minimum fill in DASHSCOPE_API_KEY (or OPENAI_API_KEY), OPENAI_BASE_URL, OPENAI_MODEL, and CROSSREF_MAILTO
```

## Running the Pipeline

```bash
# Full pipeline (pauses at interactive review after QA)
python -m src.skill run                    # uses 1.docx by default
python -m src.skill run path/to/refs.docx

# Individual modules
python -m src.skill parse                    # .docx/.txt → data/01_raw/refs_raw.json
python -m src.skill llm                    # LLM structured → data/02_llm/llm_results.json
python -m src.skill search                  # API cascade + MCP → data/03_search/search_results.json
python -m src.skill search --no-mcp        # skip MCP Step 4
python -m src.skill review                  # LLM QA judgment → qa_results.json + qa_review.json
python -m src.skill review --review        # interactive review (resumable; run after 'review')
python -m src.skill review --approve        # merge approved → qa_approved.json + bib_export_report.md
python -m src.skill export                  # → data/05_export/references.bib + references_gb.txt
python -m src.skill export --format bib      # BibTeX only
python -m src.skill export --format gb       # GB/T only
```

### Required Environment Variables

All keys loaded from `config.env` in the project root. Use `from src.config import get` and call `get("KEY", default)`.

| Variable | Required | Notes |
|---|---|---|
| `DASHSCOPE_API_KEY` | One of them | Ali Bailian / Qwen; priority over `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | One of them | OpenAI, Groq, Ollama, or any compatible provider |
| `OPENAI_BASE_URL` | Yes | Provider endpoint (default: DashScope) |
| `OPENAI_MODEL` | Yes | Model name (default: `qwen-plus`) |
| `ANTHROPIC_API_KEY` | Optional | Claude as fallback LLM |
| `CROSSREF_MAILTO` | Yes | Any valid email (required by Crossref ToS) |
| `SEMANTIC_SCHOLAR_API_KEY` | Optional | Free; S2 falls back gracefully without it |
| `MINIMAX_API_KEY` | Optional | Only for MCP Step 4 web search fallback |
| `DISABLE_MCP=1` | Optional | Skip MCP Step 4 entirely |

**Note on qwen3 models**: `llm_client.py` automatically sends `enable_thinking=False` for model names starting with `qwen3` (required for non-streaming calls to work correctly).

## Project Structure

```
src/
├── skill.py                    # CLI entry point (subcommands: parse/llm/search/review/export/run)
├── config.py                   # loads config.env
└── utils/
    ├── constants.py            # SYSTEM_PROMPT, RATE_LIMIT_DELAY, LLM_TIMEOUT
    ├── journals.py             # two-way journal name ↔ abbreviation mapping
    ├── latex.py                # clean_latex()
    ├── bibtex.py               # bibtex_entry, make_bibkey
    ├── bib_search.py           # cr_query_by_doi/title, s2_query_by_doi/title,
    │                             cr_structured_search, title_similarity
    ├── formatters.py           # format_gb (GB/T 7714-2015), format_ieee, format_nature
    ├── llm_client.py           # get_llm_response (supports custom system+user prompts)
    ├── crossref_client.py      # _normalize_crossref, query_crossref_by_doi/title
    └── semantic_scholar_client.py  # _normalize_semantic_scholar, query_semantic_scholar_by_doi/title

src/modules/                    # each independently runnable
├── parser.py                   # .docx/.txt → data/01_raw/refs_raw.json
├── llm_parse.py                # LLM structured → data/02_llm/llm_results.json (stream-save, tqdm)
├── search.py                   # 4-step cascade → data/03_search/search_results.json
├── quality.py                  # LLM QA + interactive review → data/04_quality/qa_approved.json
└── export.py                   # bibliography export → data/05_export/references.bib / _gb.txt

data/                           # all generated at runtime; not committed
├── 01_raw/refs_raw.json
├── 02_llm/llm_results.json
├── 03_search/search_results.json
├── 04_quality/
│   ├── qa_results.json         # all entries + LLM QA judgment (read-only record)
│   ├── qa_review.json           # medium+low only; dict keyed by ref_id (interactive review target)
│   ├── qa_review.json.bak      # auto-backup before overwrite
│   ├── qa_approved.json        # high + approved medium/low (after --approve)
│   ├── manual_research.json    # manual research input; user fills research_text per ref_id
│   └── manual_review.json      # parsed + QA'd manual entries (medium+low interactive review)
└── 05_export/
    ├── references.bib
    ├── references_gb.txt
    └── bib_export_report.md    # merged approval decisions + export warnings
```

## Pipeline Data Flow

```
input file (.docx/.txt)
  │
  ▼
parser.py          →  data/01_raw/refs_raw.json
  │
  ▼
llm_parse.py      →  data/02_llm/llm_results.json      (parallel, crash-safe stream-save)
  │
  ▼
search.py          →  data/03_search/search_results.json   (4-step cascade, stream-save)
  │  Step 1: DOI exact lookup        (CR + S2 parallel)
  │  Step 2: title + journal fuzzy   (CR + S2 parallel)
  │  Step 3: journal+year/vol/pages (CR only, structured)
  │  Step 4: MCP web search fallback (MiniMax MCP; skippable via --no-mcp / DISABLE_MCP=1)
  │
  ▼
quality.py         →  data/04_quality/qa_results.json  (all entries + LLM QA judgment)
  │                 data/04_quality/qa_review.json   (medium+low only, keyed by ref_id)
  │
  ▼  python -m src.modules.quality --review  →  interactive CLI (a/s/e/p/d/q)
  │
  ▼ quality --approve  →  data/04_quality/qa_approved.json
  │                   + data/05_export/bib_export_report.md
  │
  ▼
export.py          →  data/05_export/references.bib
                      data/05_export/references_gb.txt
```

## Interactive Review

```
python -m src.modules.quality              # run QA judgment (safe to re-run)
python -m src.modules.quality --review     # interactive review (resumable)
python -m src.modules.quality --approve   # merge + report
```

**qa_review.json format**: dict keyed by ref_id string. Each entry has `_approved` (null/True/False), `_decision` (pending/approved/skipped/patched), `_patch` ({field: value}) for field overrides, `_review_note`.

**Patch override**: `[E] Patch` overrides any field (title, authors, journal, year, volume, pages, doi, type). Patched entries are auto-approved and win over all other sources in export (MCP > CR > S2 > LLM).

## Manual Research Workflow

For entries skipped or still pending after review:

```
python -m src.modules.quality --manual           # init/parse+QA → manual_review.json
# → edit data/04_quality/manual_research.json: fill in research_text per ref_id
python -m src.modules.quality --manual           # parse research_text → LLM → QA
python -m src.modules.quality --manual-review    # interactive review of medium+low entries
python -m src.modules.quality --manual-approve   # merge into qa_approved.json
python -m src.skill export
```

## Architecture Notes

- **`PAPER_FIELDS`** in `export.py` includes: `authors, title, journal, year, volume, issue, pages, doi, publisher, location, edition`. All of these are extracted into `final_data` via `_best_field()`.
- **`final_data` / top-level fallback pattern**: formatters and `bibtex.py` use `entry.get("final_data", entry)` — post-merge fields preferred, graceful fallback to top-level.
- **`bibtex.py` `_read` function**: reads standard paper fields from `final_data` first, falling back to top-level entry; `api_data`, `disagreements`, `confidence`, and `ref_id` are always read directly from top-level entry regardless of `final_data`.
- **Disagreement nulling**: `_safe_api_data()` in `formatters.py` nulls `api_data` fields that are in `disagreements` before use.
- **Export field merge priority (manual > CR > S2 > LLM > MCP)**: `_best_field()` in `export.py` picks the first non-null value — not averaged or voted on. Manual data (human-reviewed) wins over all automated sources. `merger.py` exists but is not in the active pipeline.
- **Crash-safe stream-save**: `llm_parse.py` and `search.py` write to a temp file every 20 entries. A crash loses at most 19 results. Renamed to final path on success.
- **`bib_search.py` CR rate-limiting**: module-level `Semaphore(3)` in `bib_search.py` enforces ~3 Crossref req/s globally across all threads and all workers — independent of `WORKERS` in `search.py`.
- **`get_llm_response()`**: accepts optional `system=` and `user=` kwargs for custom prompts (bypasses `SYSTEM_PROMPT` template).
- **`_check_warnings()` in export.py**: type-aware warning checks. Book entries require publisher/location/edition; article/incollection require pages or doi; inproceedings requires publisher and pages or doi. `_generate_export_report()` in quality.py rebuilds `final_data` internally with the same priority and calls `_check_warnings()` to populate the report.
- **Review CLI** (`_print_field_table`): terminal-width-aware wrapping — uses `shutil.get_terminal_size()` to compute column widths dynamically. `_print_manual_card` similarly uses `_fmt` with all fields including publisher/location.
- **MCP Step 4 behavior** (`_mcp_search` in search.py): invokes MiniMax MCP server via `uvx minimax-coding-plan-mcp` stdio JSON-RPC; searches for the paper title, extracts DOI from organic results, then queries Crossref for structured data. Falls back to extracted snippet data if no Crossref DOI match. Requires `pip install mcp` and `MINIMAX_API_KEY` (or `DASHSCOPE_API_KEY` as fallback). arxiv.org is accepted as a source URL; Chinese document-sharing domains are excluded.
- **`build_final_data()` in export.py**: merges manual > CR > S2 > LLM > MCP per field using `_best_field()`; review `_patch` overrides all sources and the entry is auto-approved.
- **`_call_manual_parse_llm()` in quality.py**: uses `SYSTEM_PROMPT` from `constants.py` — the LLM schema now includes publisher, location, edition fields, extracted from manual research text.

## Coding Guidelines

**Think before coding.** State assumptions explicitly. If multiple valid interpretations exist, present them. If something is unclear, ask.

**Minimum code.** No features beyond what was asked. No abstractions for single-use code. No error handling for impossible scenarios. If you write 200 lines and it could be 50, rewrite it.

**Touch only what you must.** Don't "improve" adjacent code. Match existing style. Remove imports/variables/functions your changes made unused, but don't remove pre-existing dead code unless asked.

**Verify your work.** Define success criteria upfront. For multi-step tasks, state a brief plan with explicit checks before moving on.