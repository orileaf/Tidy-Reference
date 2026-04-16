#!/usr/bin/env python3
"""
src/modules/export.py — Generate formatted bibliography from QA-approved results.

Input:  data/qa_approved.json  (high-confidence auto-approved + manually approved)
Output:
  data/references.bib           — BibTeX entries
  data/references_gb.txt       — GB/T 7714-2015 formatted references
  data/stage3_warnings_report.md — per-entry format warnings

Field priority (best → fallback):
  MCP > Crossref > Semantic Scholar > LLM-parsed

CLI:
  python -m src.modules.export
  python -m src.modules.export --format bib     # BibTeX only
  python -m src.modules.export --format gb      # GB/T only
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.utils.bibtex import bibtex_entry, make_bibkey
from src.utils.formatters import format_gb, _detect_ambiguous_author

# ── Config ─────────────────────────────────────────────────────────────────────

APPROVED_JSON = "data/04_quality/qa_approved.json"
OUT_BIB = "data/05_export/references.bib"
OUT_GB = "data/05_export/references_gb.txt"

PAPER_FIELDS = ["authors", "title", "journal", "year", "volume", "issue", "pages", "doi", "publisher", "location", "edition"]


# ── Field merging ──────────────────────────────────────────────────────────────

def _best_field(sources: dict, field: str):
    """Return the best available value for a field from sources.

    Priority: manual > Crossref > Semantic Scholar > LLM-parsed > MCP.
    Returns (value, source_tag).
    """
    for tag, src in [("manual", sources.get("manual")),
                     ("crossref", sources.get("crossref")),
                     ("semantic_scholar", sources.get("semantic_scholar")),
                     ("llm", sources.get("llm")),
                     ("mcp", sources.get("mcp"))]:
        v = (src or {}).get(field) if isinstance(src, dict) else None
        if v is not None and str(v).strip():
            return str(v).strip(), tag
    return None, "null"


def build_final_data(entry: dict) -> tuple[dict, dict]:
    """Build final_data dict and field_confidence dict from a search+QA entry.

    Returns (final_data, field_confidence).
    """
    sources = {
        "mcp": entry.get("mcp"),
        "crossref": entry.get("crossref"),
        "semantic_scholar": entry.get("semantic_scholar"),
        "llm": entry.get("llm_data"),
        "manual": entry.get("manual_data"),
    }

    final_data = {}
    field_confidence = {}

    for field in PAPER_FIELDS:
        val, src = _best_field(sources, field)
        # Review patch overrides all sources (highest priority)
        patch = entry.get("_patch") or {}
        if field in patch and patch[field]:
            val = str(patch[field])
            src = "patch"
        final_data[field] = val
        fc_map = {"mcp": "raw", "crossref": "crossref",
                  "semantic_scholar": "semantic_scholar", "llm": "raw",
                  "manual": "raw", "patch": "patch", "null": "null"}
        field_confidence[field] = fc_map.get(src, "null")

    # type: patch > LLM
    patch_type = (entry.get("_patch") or {}).get("type")
    final_data["type"] = patch_type or (sources["llm"].get("type") if sources.get("llm") else None)
    field_confidence["type"] = "patch" if patch_type else ("raw" if final_data.get("type") else "null")

    return final_data, field_confidence


# ── Warnings ───────────────────────────────────────────────────────────────────

def _check_warnings(entry: dict, final_data: dict) -> list[str]:
    """Return a list of warning strings for one entry.

    Reports: missing fields, type mismatch, MCP URL absence, and
    incomplete MCP results (MCP used but missing key structured fields).
    """
    warnings = []
    strategy = entry.get("strategy_used", "")

    # ── Missing fields (type-aware) ─────────────────────────────────────────────
    ref_type = final_data.get("type") or ""
    if not ref_type:
        warnings.append("missing_type")
    if not final_data.get("title"):
        warnings.append("missing_title")
    if not final_data.get("journal"):
        warnings.append("missing_journal")
    if not final_data.get("year"):
        warnings.append("missing_year")
    if not final_data.get("volume"):
        warnings.append("missing_volume")
    if not final_data.get("doi"):
        warnings.append("missing_doi")

    # ── Type-specific required fields ───────────────────────────────────────
    if ref_type == "article" or ref_type == "incollection":
        if not final_data.get("pages") and not final_data.get("doi"):
            warnings.append("missing_pages_and_doi")
        if not final_data.get("pages"):
            warnings.append("missing_pages")
    elif ref_type in ("inproceedings", "conference", "proceedings"):
        if not final_data.get("pages") and not final_data.get("doi"):
            warnings.append("missing_pages_and_doi")
        if not final_data.get("publisher"):
            warnings.append("missing_publisher")
    elif ref_type == "book":
        if not final_data.get("publisher"):
            warnings.append("missing_publisher")
        if not final_data.get("location"):
            warnings.append("missing_location")
        if not final_data.get("edition"):
            warnings.append("missing_edition")

    # ── Type mismatch: LLM says article/book but CR says otherwise ─────────────
    llm_type = (entry.get("llm_data") or {}).get("type", "").lower()
    cr_type = (entry.get("crossref") or {}).get("type", "").lower()
    if llm_type == "article" and cr_type in ("book-chapter", "book", "proceedings"):
        warnings.append(f"type_mismatch:llm={llm_type} cr={cr_type}")
    elif llm_type in ("book", "book-chapter") and cr_type == "article":
        warnings.append(f"type_mismatch:llm={llm_type} cr={cr_type}")

    # ── MCP fallback: incomplete result ────────────────────────────────────────
    if strategy == "mcp_fallback":
        mcp_data = entry.get("mcp") or {}
        mcp_url = mcp_data.get("source_url")
        mcp_doi = mcp_data.get("doi")
        # URL and DOI are the most important provenance markers for MCP results
        if not mcp_url and not mcp_doi:
            warnings.append("mcp_no_url_or_doi")
        # MCP result is incomplete if it's missing 3+ of [journal, year, volume, pages, doi]
        mcp_fields = [mcp_data.get("journal"), mcp_data.get("year"),
                      mcp_data.get("volume"), mcp_data.get("pages"), mcp_doi]
        missing_count = sum(1 for v in mcp_fields if not v)
        if missing_count >= 3:
            warnings.append(f"mcp_incomplete:{missing_count}/5 fields missing")
        if mcp_url:
            warnings.append(f"mcp_url:{mcp_url[:80]}")

    # ── Ambiguous author format ─────────────────────────────────────────────────
    authors = final_data.get("authors") or ""
    if authors:
        for part in re.split(r"\s+and\s+", authors):
            part = part.strip()
            if part and _detect_ambiguous_author(part):
                warnings.append(f"ambiguous_author:{part[:30]}")
                break
    return warnings


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    project_root = Path(__file__).parent.parent.parent
    in_path = project_root / APPROVED_JSON

    formats = set()
    if "--format" in sys.argv:
        idx = sys.argv.index("--format")
        formats = set(sys.argv[idx + 1].split(",")) if idx + 1 < len(sys.argv) else {"bib", "gb"}
    else:
        formats = {"bib", "gb"}

    if not in_path.exists():
        print(f"ERROR: {in_path} not found — run:")
        print(f"  python -m src.modules.quality")
        print(f"  python -m src.modules.quality --approve")
        sys.exit(1)

    with open(in_path, encoding="utf-8") as f:
        approved = json.load(f)

    print(f"Loaded {len(approved)} approved entries from {in_path}")

    # Build final_data for each entry
    entries = []
    used_bib_keys = {}

    for raw_entry in approved:
        entry = dict(raw_entry)
        final_data, field_confidence = build_final_data(entry)
        entry["final_data"] = {k: v for k, v in final_data.items() if v is not None}
        entry["field_confidence"] = field_confidence
        # Confidence from QA
        qa = entry.get("qa", {})
        entry["confidence"] = qa.get("confidence", "high")
        entry["disagreements"] = qa.get("disagreed_fields", [])
        entries.append(entry)

    # ── BibTeX ───────────────────────────────────────────────────────────────
    if "bib" in formats:
        bib_path = project_root / OUT_BIB
        bib_path.parent.mkdir(parents=True, exist_ok=True)
        bib_lines = [
            f"% references.bib — generated by src/modules/export.py",
            f"% {len(entries)} entries",
            "",
        ]
        for entry in entries:
            # bibtex_entry expects: ref_id, final_data, api_data, confidence, disagreements
            # Patch api_data from the raw channels
            entry_for_bib = dict(entry)
            entry_for_bib["api_data"] = {
                k: v for k, v in {
                    "crossref": entry.get("crossref"),
                    "semantic_scholar": entry.get("semantic_scholar"),
                }.items() if v is not None
            }
            bib_lines.append(bibtex_entry(entry_for_bib, used_bib_keys))
            bib_lines.append("")
        with open(bib_path, "w", encoding="utf-8") as f:
            f.write("\n".join(bib_lines))
        print(f"Wrote → {bib_path}")

    # ── GB/T 7714-2015 ───────────────────────────────────────────────────────
    if "gb" in formats:
        gb_path = project_root / OUT_GB
        gb_path.parent.mkdir(parents=True, exist_ok=True)
        gb_lines = []
        for entry in entries:
            # Strip top-level ref_id so format_gb uses ref_num parameter exclusively
            gb_entry = {k: v for k, v in entry.items() if k != "ref_id"}
            gb_lines.append(format_gb(gb_entry, entry.get("ref_id", 0)))
        with open(gb_path, "w", encoding="utf-8") as f:
            f.write("\n".join(gb_lines))
        print(f"Wrote → {gb_path}")

    print(f"\n══ Export done: {len(entries)} entries ══")


if __name__ == "__main__":
    main()
