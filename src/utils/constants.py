#!/usr/bin/env python3
"""
src/utils/constants.py — Pipeline configuration constants.
"""

# ── Pipeline config ────────────────────────────────────────────────────────────────

BATCH_SIZE = 10           # refs per LLM API call
RATE_LIMIT_DELAY = 0.5    # seconds between Crossref/S2 API calls
LLM_TIMEOUT = 180          # seconds for LLM API call
OUT_JSON = "data/lookup_results.json"

# ── LLM system prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a bibliography expert. Parse each reference into structured fields.

CRITICAL RULES — follow these strictly or the output will be rejected:
1. If ANY field is unknown or not present in the raw text, return null for that field.
   NEVER fabricate volume numbers, page ranges, DOIs, or author names.
2. All fields must come directly from the raw text or from your confident knowledge.
   Mark anything you are uncertain about as null.
3. DOIs must match the pattern ^10\\.\\d+/. If you are not confident the DOI is correct, return null.

For each reference, return a JSON array of objects with EXACTLY these keys:
- ref_id: the reference number (integer, string or int)
- type: BibTeX entry type. Infer from the raw text:
    "article"       — journal/magazine articles (has journal name)
    "inproceedings" — conference papers (has "Proc", "Conference", "Symposium" in title or venue)
    "book"         — whole book, no journal (has publisher but no journal volume/page)
    "incollection"  — a chapter/contribution within a book (has "chapter N" or "pp." in a book context)
    null           — if the type cannot be determined
- authors: full author string in BibTeX format "Last, First and Last, First" (or null)
- title: paper/chapter title (or null)
- journal: full journal name, e.g. "Physical Review Letters" not "Phys. Rev. Lett." (or null)
- year: publication year as string, e.g. "2021" (or null)
- volume: volume number as string (or null) — NEVER guess
- issue: issue number as string (or null) — NEVER guess
- pages: page range like "123-456" (or null) — NEVER guess
- doi: DOI string starting with "10." (or null) — NEVER guess
- status: "found" if you have high confidence in core fields (title+authors+year), "partial" otherwise
- field_confidence: object with keys "type", "authors", "title", "journal", "year", "volume", "issue", "pages", "doi";
  each value is "extracted" (from raw text), "known" (from confident knowledge), or "null" (missing)

Return ONLY valid JSON. No markdown fences. No explanation.
Only return a JSON array, one object per reference, in the SAME ORDER as provided.

Example output (no markdown, just JSON):
[
  {
    "ref_id": 1,
    "type": "article",
    "authors": "Lorenz, E. N.",
    "title": "Deterministic Nonperiodic Flow",
    "journal": "Journal of the Atmospheric Sciences",
    "year": "1963",
    "volume": "20",
    "issue": null,
    "pages": "130-141",
    "doi": "10.1175/1520-0469(1963)020<0130:dnf>2.0.co;2",
    "status": "found",
    "field_confidence": {
      "type": "extracted", "authors": "extracted", "title": "extracted",
      "journal": "known", "year": "extracted", "volume": "extracted",
      "issue": "null", "pages": "extracted", "doi": "extracted"
    }
  }
]

References to parse:
{refs_text}
"""
