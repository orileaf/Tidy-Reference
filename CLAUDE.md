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
│   ├── qa_approved.json       # high + approved medium/low (after --approve)
│   ├── manual_research.json   # manual research input; user fills research_text per ref_id
│   └── manual_review.json     # parsed + QA'd manual entries (medium+low interactive review)
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
python -m src.modules.quality --approve   # merge + report
```

**qa_review.json format**: dict keyed by ref_id string. Each entry has `_approved` (null/True/False), `_decision` (pending/approved/skipped/patched), `_patch` ({field: value}) for field overrides, `_review_note`.

**Patch override**: `[E] Patch` overrides any field (title, authors, journal, year, volume, pages, doi, type). Patched entries are auto-approved and win over all other sources in export (MCP > CR > S2 > LLM).

## Manual Research Workflow (for skipped/pending entries)

After interactive review, any skipped/pending entries can be manually researched and re-fed through the pipeline:

```
python -m src.modules.quality --manual           # init/parse+QA → manual_review.json
# → edit data/04_quality/manual_research.json: fill in research_text per ref_id
python -m src.modules.quality --manual           # parse research_text → LLM → QA → manual_review.json
python -m src.modules.quality --manual-review    # interactive review of medium+low entries
python -m src.modules.quality --manual-approve   # merge into qa_approved.json
python -m src.skill export                        # export bibliography
```

Or via the skill CLI:
```
python -m src.skill review --manual
# fill in research_text
python -m src.skill review --manual
python -m src.skill review --manual-review
python -m src.skill review --manual-approve
```

**manual_research.json**: dict keyed by ref_id. Each entry has `ref_id`, `research_text` (user fills with BibTeX or plain text), and `parsed` (filled by pipeline). Re-running `--manual` preserves existing `research_text` values.

## Search Cascade (search.py)

Four steps tried **in order** until data is found. Within each step, CR and S2 are fired in parallel (both run, first to respond wins, the other result is discarded):

| Step | Method | Sources |
|------|--------|---------|
| 1 | DOI exact lookup | CR + S2 (parallel) |
| 2 | title + journal fuzzy search | CR + S2 (parallel) |
| 3 | journal + year/vol/pages structured search | CR only |
| 4 | MCP agent (web search) | MiniMax MCP stdio JSON-RPC; skippable via `--no-mcp` or `DISABLE_MCP=1` |

**Result validation (Steps 2 & 3)**: CR results accepted only if:
1. Title Jaccard similarity (token-based, stopword-filtered) ≥ 0.30 vs query title
2. CR journal normalizes to contain (or be contained by) the query journal

`strategy_used` records which step succeeded. Results stored per-channel in `search_results.json` (crossref / semantic_scholar / mcp keys), not merged at this stage.

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
- **`bibtex.py` `_read` function**: reads standard paper fields from `final_data` first, falling back to top-level entry; `api_data`, `disagreements`, `confidence`, and `ref_id` are always read directly from top-level entry regardless of `final_data`.
- **Disagreement nulling**: `_safe_api_data()` in `formatters.py` nulls `api_data` fields that are in `disagreements` before use.
- **Export field merge priority (MCP > CR > S2 > LLM)**: `_best_field()` in `export.py` picks the first non-null value — not averaged or voted on. Inline in `export.py`; `merger.py` exists but is not in the active pipeline.
- **Crash-safe stream-save**: `llm_parse.py` and `search.py` write to a temp file every 20 entries. A crash loses at most 19 results. Renamed to final path on success.
- **`bib_search.py` CR rate-limiting**: module-level `Semaphore(3)` in `bib_search.py` enforces ~3 Crossref req/s globally across all threads and all workers — independent of `WORKERS` in `search.py`.
- **`get_llm_response()`**: accepts optional `system=` and `user=` kwargs for custom prompts (bypasses `SYSTEM_PROMPT` template).
- Reference header patterns detected: `[N]`, `[N].`, `N.`, bare `N ` before a capital letter.
- `llm_parse.py` validates LLM output: DOI regex, 4-digit year. Falls back to skeleton on failure; failed batches retry single-ref.
- **Review CLI** (`_print_field_table`): terminal-width-aware wrapping — uses `shutil.get_terminal_size()` to compute column widths dynamically. Long titles/authors wrap at word boundaries instead of overflowing.
- **MCP Step 4 behavior** (`_mcp_search` in search.py): invokes MiniMax MCP server via `uvx minimax-coding-plan-mcp` stdio JSON-RPC; searches for the paper title, extracts DOI from organic results, then queries Crossref for structured data. Falls back to extracted snippet data if no Crossref DOI match. Requires `pip install mcp` and `MINIMAX_API_KEY` (or `DASHSCOPE_API_KEY` as fallback). arxiv.org is accepted as a source URL; Chinese document-sharing domains are excluded.
- **`build_final_data()` in export.py**: merges MCP > CR > S2 > LLM per field using `_best_field()`; review `_patch` overrides all sources and the entry is auto-approved.

---

## Behavioral Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.