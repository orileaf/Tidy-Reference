# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A bibliography enrichment tool for scientific papers. Extracts references from a `.docx`/`.txt` file, parses them into structured fields with an LLM, cross-verifies via Crossref + Semantic Scholar APIs (with an optional MCP web-search fallback), runs LLM-powered quality assessment, and exports formatted BibTeX and GB/T 7714-2015 bibliographies.

## Running the Pipeline

Use the skill CLI (`src/skill.py`) or the shell scripts in `scripts/`:

```bash
# Full pipeline
python -m src.skill run                    # uses 1.docx by default
python -m src.skill run path/to/refs.docx

# Individual modules
python -m src.skill parse                    # .docx/.txt → data/01_raw/refs_raw.json
python -m src.skill parse data/refs.txt    # custom input
python -m src.skill llm                    # LLM structured → data/02_llm/llm_results.json
python -m src.skill search                  # API cascade + MCP → data/03_search/search_results.json
python -m src.skill search --no-mcp        # skip MCP Step 4
python -m src.skill review                  # LLM QA → qa_results.json + qa_review.json
python -m src.skill review --approve        # merge approved → qa_approved.json
python -m src.skill export                  # → data/05_export/references.bib + references_gb.txt
python -m src.skill export --format bib      # BibTeX only
python -m src.skill export --format gb       # GB/T only
```

### Required Environment Variables

All keys loaded from `config.env` in the project root (`cp config.env.example config.env`, then edit).

**LLM API**: uses the OpenAI SDK — works with any OpenAI-compatible provider.
Priority: `DASHSCOPE_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY`.

| Variable | Required | Notes |
|---|---|---|
| `DASHSCOPE_API_KEY` | One of them | Ali Bailian / Qwen; priority over `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | One of them | OpenAI, Groq, Ollama, or any compatible provider |
| `OPENAI_BASE_URL` | Yes | Your provider's endpoint (default: DashScope) |
| `OPENAI_MODEL` | Yes | Model name sent to the API (default: `qwen-plus`) |
| `ANTHROPIC_API_KEY` | Optional | Claude as fallback LLM |
| `CROSSREF_MAILTO` | Yes | Any valid email (required by Crossref ToS) |
| `SEMANTIC_SCHOLAR_API_KEY` | Optional | Free; S2 falls back gracefully without it |
| `MINIMAX_API_KEY` | Optional | Only for MCP Step 4 web search fallback |
| `PIPTHON` | Optional | Python interpreter for shell scripts; defaults to `python3` |
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
    ├── bibtex.py               # bibtex_entry, make_bibkey (uses final_data / api_data fallback)
    ├── bib_search.py           # cr_query_by_doi/title, s2_query_by_doi/title, score_result,
    │                             cr_structured_search, title_similarity (shared by search module)
    ├── formatters.py           # format_gb (GB/T 7714-2015), format_ieee, format_nature
    │                             (_safe_api_data nulls disagreed fields; code kept for recovery)
    ├── llm_client.py           # get_llm_response; supports custom system+user prompts
    ├── crossref_client.py      # _normalize_crossref, query_crossref_by_doi/title
    └── semantic_scholar_client.py  # _normalize_semantic_scholar, query_semantic_scholar_by_doi/title

src/modules/                    # Skill modules (each independently runnable)
├── __init__.py
├── parser.py                   # .docx/.txt → data/01_raw/refs_raw.json
├── llm_parse.py                # LLM structured → data/02_llm/llm_results.json (stream-save, tqdm)
├── search.py                   # 4-step cascade → data/03_search/search_results.json
├── quality.py                  # LLM QA + interactive review → data/04_quality/qa_approved.json
└── export.py                   # bibliography export → data/05_export/references.bib / _gb.txt

scripts/
├── run_all.sh                  # equivalent to `python -m src.skill run`; input defaults to 1.docx
├── run_parser.sh / run_llm.sh / run_search.sh / run_review.sh / run_export.sh  # individual steps

data/                           # all generated at runtime; not committed
├── 01_raw/refs_raw.json
├── 02_llm/llm_results.json
├── 03_search/search_results.json
├── 04_quality/
│   ├── qa_results.json        # all entries + LLM QA judgment (read-only record)
│   ├── qa_review.json         # medium+low only; dict keyed by ref_id (interactive review target)
│   ├── qa_review.json.bak    # auto-backup before overwrite
│   └── qa_approved.json       # high + approved medium/low (after --approve)
├── 05_export/
│   ├── references.bib
│   ├── references_gb.txt
│   └── bib_export_report.md   # merged approval decisions + export warnings
└── qa_medium.json / qa_low.json  # legacy; auto-migrated to qa_review.json on first --approve
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

## Interactive Review Workflow

```
python -m src.modules.quality              # run QA judgment (safe to re-run)
python -m src.modules.quality --review     # interactive review (resumable)
python -m src.modules.quality --approve    # merge + bib_export_report.md
```

**qa_review.json format**: dict keyed by ref_id string. Each entry has `_approved` (null/True/False), `_decision` (pending/approved/skipped/patched), `_patch` ({field: value}) for field overrides, `_review_note`.

**Patch override**: `[E] Patch` overrides any field (title, authors, journal, year, volume, pages, doi, type). Patched entries are auto-approved and win over all other sources in export (MCP > CR > S2 > LLM).

## Search Cascade (search.py)

Four steps tried **in order** until data is found:

| Step | Method | Sources |
|------|--------|---------|
| 1 | DOI exact lookup | CR + S2 (parallel within step) |
| 2 | title + journal fuzzy search | CR + S2 (parallel within step) |
| 3 | journal + year/vol/pages structured search | CR only |
| 4 | MCP agent (web search) | MiniMax MCP via `mcp.ClientSession` stdio JSON-RPC; skippable via `--no-mcp` or `DISABLE_MCP=1` |

**Result validation (Steps 2 & 3)**: CR results accepted only if:
1. Title Jaccard similarity (token-based, stopword-filtered) ≥ 0.30 vs query title
2. CR journal normalizes to contain (or be contained by) the query journal

Results stored **per-channel** (crossref / semantic_scholar / mcp keys in `search_results.json`), not merged. `strategy_used` records which step succeeded.

## LLM QA (quality.py)

Each batch sends all channels of retrieved data + raw_text to the LLM.
Returns: `{"confidence": "high"|"medium"|"low", "reason": "...", "agreed_fields": [...], "disagreed_fields": [...]}`.

| Level | Meaning |
|-------|---------|
| high | Retrieval matches original citation. Minor differences that do NOT lower confidence: page range overlap (result 815-820 contains cited page 818), author name format variants ("Tang X" vs "Xi Tang" — same person, different conventions). |
| medium | Significant inconsistency but core info inferable: year off by 1, pages don't overlap, author surnames don't match |
| low | Title completely different (Jaccard < 0.3), or unrelated paper returned |

High entries are **auto-approved**. Medium and low require manual review.

## Warnings Report (`bib_export_report.md`)

Generated by `quality.py --approve`. Reports all information incompleteness per approved entry:

| Warning | Meaning |
|---------|---------|
| `missing_type` | Document type (article/book/chapter) could not be determined |
| `missing_title/year/journal/volume/pages/doi` | Required bibliographic field is absent |
| `mcp_no_url_or_doi` | MCP used but neither URL nor DOI found |
| `mcp_incomplete:N/5 fields missing` | MCP used; 3+ of journal/year/volume/pages/doi absent |
| `mcp_url:...` | MCP found a source URL — click to verify |
| `type_mismatch:llm=X cr=Y` | LLM type conflicts with Crossref type |
| `ambiguous_author:...` | Author name has unusual format |

## Config API

Use `from src.config import get` and call `get("KEY", default)` — merges `config.env` with environment variables.

## Architecture Notes

- **`final_data` / top-level fallback pattern**: formatters and `bibtex.py` use `entry.get("final_data", entry)` — post-merge fields preferred, graceful fallback to top-level.
- **`bibtex.py` `_read` function**: standard paper fields read from `final_data` first, then top-level; `api_data`, `disagreements`, `confidence`, and `ref_id` always read from top-level directly.
- **Disagreement nulling**: `_safe_api_data()` in `formatters.py` nulls `api_data` fields that are in `disagreements` before use.
- **Export field merge priority (MCP > CR > S2 > LLM)**: `_best_field()` in `export.py` picks the first non-null value — not averaged or voted on. Inline in `export.py`; `merger.py` exists but is not in the active pipeline.
- **Crash-safe stream-save**: `llm_parse.py` and `search.py` write to a temp file every 20 entries. A crash loses at most 19 results. Renamed to final path on success.
- **`bib_search.py` CR rate-limiting**: shared threading `Semaphore` enforces ~3 Crossref req/s across all workers (`CR_CONCURRENCY=3`, `CR_RATE_SLEEP=0.34s`). Not affected by `WORKERS` in `search.py` or `LLM_MAX_CONCURRENCY` in `llm_parse.py`.
- **`get_llm_response()`**: accepts optional `system=` and `user=` kwargs for custom prompts (bypasses `SYSTEM_PROMPT` template).
- Reference header patterns detected: `[N]`, `[N].`, `N.`, bare `N ` before a capital letter.
- `llm_parse.py` validates LLM output: DOI regex, 4-digit year. Falls back to skeleton on failure; failed batches retry single-ref.
