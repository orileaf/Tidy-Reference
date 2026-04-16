# tidy-reference

A bibliography enrichment tool for scientific papers. Drop in a `.docx` or `.txt` file full of raw citations, and this tool parses them, looks up each reference via Crossref and Semantic Scholar, quality-checks the results with an LLM, and exports clean BibTeX and GB/T 7714-2015 formatted bibliographies.

---

## What it does

If you've ever had a reference list like this:

```
[1] J. Smith, "Deep learning in optics," Nature Photonics, vol. 15, pp. 312-320, 2021.
[2] Li, Wei, and Y. Zhang. "Quantum cascade lasers: a review." IEEE J. Sel. Top. Quantum Electron. 28, 2022.
```

...and needed to turn it into a proper, verified bibliography, this tool does that for you — end-to-end, with human-in-the-loop quality checks.

---

## Features

- **Parses** raw citation text from `.docx` or `.txt` files (handles `[1]`, `1.`, bare-number formats)
- **Structured extraction** with any OpenAI-compatible LLM API — fields include: title, authors, journal, year, volume, issue, pages, doi, publisher, location, edition
- **4-step search cascade**: DOI lookup → title+journal fuzzy match → structured journal search → MCP web search fallback
- **LLM quality assessment** classifies each result as high / medium / low confidence
- **Interactive review** — you approve or patch medium/low entries before export; patch supports all fields including publisher, location, edition
- **Dual output**: BibTeX (`.bib`) + GB/T 7714-2015 plain-text bibliography
- **Type-aware export warnings**: checks required fields per entry type (e.g. edition/publisher/location for books, pages or doi for articles)
- **Crash-safe**: results saved incrementally, no data loss on interruption

---

## Prerequisites

### Python

Python 3.10 or later.

### API Keys

Only two keys are truly required:

| Key | Required | Where to get |
|-----|---------|-------------|
| **Any OpenAI-compatible API key** | Yes | DashScope, OpenAI, Groq, Ollama, or any compatible provider — see [LLM Configuration](#llm-configuration) below |
| `CROSSREF_MAILTO` | Yes | Any valid email address (Crossref ToS requires it) |
| `MINIMAX_API_KEY` | **Optional** | Only needed for the MCP Step 4 web search fallback; see [below](#mcp-step-4-optional) |
| `SEMANTIC_SCHOLAR_API_KEY` | No | [Semantic Scholar](https://www.semanticscholar.org/product/api) (free, works without a key but is rate-limited) |

---

## LLM Configuration

This tool uses the **OpenAI SDK**, so it works with any provider that speaks the OpenAI-compatible API format:

| Provider | API Key Variable | Base URL |
|----------|-----------------|----------|
| [Aliyun DashScope](https://dashscope.console.aliyun.com/) | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| [OpenAI](https://platform.openai.com/) | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| [Groq](https://console.groq.com/) | `OPENAI_API_KEY` | `https://api.groq.com/openai/v1` |
| [Ollama](https://ollama.com/) (local) | `OPENAI_API_KEY` (any value) | `http://localhost:11434/v1` |
| Any other OpenAI-compatible | `OPENAI_API_KEY` | Provider's endpoint |

Priority: `DASHSCOPE_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY`

Set these in `config.env` (see [Installation](#installation) below).

### MCP (Step 4, Optional)

MCP is a **web search fallback** — it only activates when the first 3 search steps (DOI, title, journal) all return no results. It is entirely optional.

To enable it:
1. Copy `.mcp.json.example` → `.mcp.json`
2. Open `.mcp.json` and replace `/path/to/your/tidy-reference` with your actual project path
3. Install MiniMax MCP: `pip install mcp` (or `uvx minimax-coding-plan-mcp -y -`)
4. Set `MINIMAX_API_KEY` in `config.env`

To **skip MCP entirely** (recommended if you don't need Step 4):
```bash
python -m src.skill search --no-mcp
# or set DISABLE_MCP=1 in config.env
```

---

## Installation

```bash
# 1. Clone the repository
git clone <your-repo-url>
cd tidy-reference

# 2. Install Python dependencies
pip install python-docx requests tqdm openai

# 3. Configure API keys
cp config.env.example config.env
# Edit config.env — at minimum fill in:
#   OPENAI_API_KEY (or DASHSCOPE_API_KEY)
#   OPENAI_BASE_URL
#   OPENAI_MODEL
#   CROSSREF_MAILTO
```

> If `python3` is not on your PATH, open `config.env` and set `PIPTHON` to your Python executable path.

---

## Quick Start

### Step 0 — Prepare your input file

Put your `.docx` or `.txt` file anywhere on your computer. The project default input is `1.docx` in the project root, but you can pass any path:

```bash
# Default: looks for 1.docx in the project root
python -m src.skill run

# Or specify your file directly
python -m src.skill run /absolute/path/to/your_refs.docx
python -m src.skill run ./my_references.txt
```

The file should contain a plain reference list — one reference per line. The tool automatically detects common numbering formats (`[1]`, `1.`, `1 `, etc.).

### Step 1 — Run the full pipeline

```bash
python -m src.skill run your_refs.docx
```

This runs all stages automatically. When it finishes the quality judgment, it pauses and tells you:

```
Next steps:
  python -m src.skill review --review   # review uncertain entries
  python -m src.skill review --approve   # merge + generate report
  python -m src.skill export             # export bibliography
```

### Step 2 — Review uncertain entries

Medium and low confidence entries need your judgment. Run interactive review:

```bash
python -m src.skill review --review
```

You will see each uncertain entry with its original citation and retrieved data. Press `a` to approve, `e` to edit a field, `d` to skip, `q` to save and quit.

### Step 3 — Export

```bash
python -m src.skill export
```

Output files appear in `data/05_export/`:
- `references.bib` — BibTeX format
- `references_gb.txt` — GB/T 7714-2015 format
- `bib_export_report.md` — warnings and notes per entry

---

## Step-by-Step Reference

The full pipeline is composed of 5 independent stages. Run them one at a time for more control:

```bash
# Stage 1 — Parse: extract raw text from your document
python -m src.skill parse your_refs.docx
# Output: data/01_raw/refs_raw.json

# Stage 2 — LLM Structured Extraction
python -m src.skill llm
# Output: data/02_llm/llm_results.json

# Stage 3 — Search Cascade (Steps 1–3 of the cascade; no MCP)
python -m src.skill search --no-mcp
# Output: data/03_search/search_results.json

# Stage 4 — Quality Judgment (LLM assesses every entry)
python -m src.skill review
# Output: data/04_quality/qa_results.json + qa_review.json

# Stage 5 — Merge + Export
python -m src.skill review --approve   # merge approved entries first
python -m src.skill export             # then export
```

Or use the shell script shortcuts:

```bash
bash scripts/run_all.sh              # Stages 1–4, pauses after QA
bash scripts/run_review.sh --review   # interactive review
bash scripts/run_review.sh --approve  # merge + report
bash scripts/run_export.sh            # export bibliography
```

---

## Output Files

| File | Description |
|------|-------------|
| `data/01_raw/refs_raw.json` | Raw citation text extracted from your document |
| `data/02_llm/llm_results.json` | LLM-parsed structured fields (title, authors, journal…) |
| `data/03_search/search_results.json` | API lookup results by channel (Crossref / Semantic Scholar / MCP) |
| `data/04_quality/qa_approved.json` | Approved entries ready for export (includes type, publisher, location, edition for books) |
| `data/05_export/references.bib` | BibTeX bibliography |
| `data/05_export/references_gb.txt` | GB/T 7714-2015 bibliography |
| `data/05_export/bib_export_report.md` | Warnings by entry type: missing required fields, type mismatches, MCP URL checks, ambiguous authors |

---

## Interactive Review Key Reference

During `review --review`:

| Key | Action |
|-----|--------|
| `a` | Approve as-is |
| `e` | Edit (patch specific fields — auto-approved after edit) |
| `p` | Preview changes |
| `d` | Skip / don't export this entry |
| `q` | Save and quit |

Patched fields (`e`) override all sources and the entry is auto-approved immediately.
Patchable fields: title, authors, journal, year, volume, pages, doi, type, **publisher**, **location**, **edition**.

**For entries that could not be resolved** (skipped during review), fill in `research_text` in
`data/04_quality/manual_research.json`, then run `python -m src.skill review --manual` to re-parse
and re-QA them.
