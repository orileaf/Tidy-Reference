#!/usr/bin/env python3
"""
src/utils/merger.py — Result merging, confidence scoring, and title extraction.
"""

import re

# Standard field keys in raw_data / api_data / final_data
PAPER_FIELDS = ["authors", "title", "journal", "year", "volume", "issue", "pages", "doi"]
# 'type' comes only from LLM, not API sources
EXTRA_FIELDS = ["type"]


def _raw_text_has_field(raw_text: str, field: str, value: str) -> bool:
    """Check if raw_text explicitly mentions a field value (for validation)."""
    if not raw_text or not value:
        return False
    value_lower = value.lower()
    if field == "volume":
        patterns = [
            rf'\bvol\.?\s*{re.escape(value_lower)}\b',
            rf'\bvolume\s*{re.escape(value_lower)}\b',
        ]
    elif field == "pages":
        patterns = [rf'\bp\.? ?{re.escape(value_lower)}\b']
    elif field == "journal":
        patterns = [rf'\b{re.escape(value_lower)}\b']
    elif field == "year":
        patterns = [rf'\b{re.escape(value_lower)}\b']
    else:
        return value_lower in raw_text.lower()
    return any(re.search(p, raw_text.lower()) for p in patterns)


def _get_field_values(api_result: dict | None, field: str) -> list:
    """Collect non-None values for a field from all API sources."""
    if api_result is None:
        return []
    vals = []
    for source_data in api_result.values():
        if isinstance(source_data, dict):
            v = source_data.get(field)
            if v is not None and str(v).strip():
                vals.append(str(v).strip())
    return vals


def _multi_source_agree(api_result: dict | None, field: str) -> tuple[str | None, bool]:
    """Return (agreed_value, is_agreed) across all sources."""
    vals = _get_field_values(api_result, field)
    if not vals:
        return None, False
    if len(set(v.lower() for v in vals)) == 1:
        return vals[0], True
    return None, False


def _pick_best_field(api_result: dict | None, field: str, raw_text: str) -> str | None:
    """Pick best value: agreed > raw_text match > first available."""
    vals = _get_field_values(api_result, field)
    if not vals:
        return None
    agreed_val, is_agreed = _multi_source_agree(api_result, field)
    if is_agreed:
        return agreed_val
    if field in ("volume", "pages") and raw_text:
        for v in vals:
            if _raw_text_has_field(raw_text, field, v):
                return v
    return vals[0]


def _source_for_field(
    raw_val: str | None, cr_val: str | None, s2_val: str | None
) -> str:
    """Return the source tag for a field: 'raw', 'crossref', 'semantic_scholar', 'null'."""
    if raw_val:
        return "raw"
    if cr_val:
        return "crossref"
    if s2_val:
        return "semantic_scholar"
    return "null"


def compute_confidence(
    raw_data: dict,
    api_result: dict | None,
    source_agreements: int,
    disagreements: list[str],
) -> str:
    """
    Compute confidence level for a merged reference entry.

    Confidence levels (in priority order):
      high   — CR and S2 both returned data AND agree on ≥2 fields
               AND neither has a "major" disagreement (title/journal/year/doi).
               Two independent sources agree → no human verification needed.
      medium — CR and S2 agree on exactly 1 field, no major disagreements.
               Some independent verification exists but not strong enough for high.
      low    — Major disagreement (CR↔S2 differ on title, journal, or year),
               OR only one API source returned data.
               Human verification needed.
      not_found — Neither API returned any data.
    """
    cr = api_result.get("crossref") if api_result else None
    s2 = api_result.get("semantic_scholar") if api_result else None
    has_api = bool(cr or s2)

    if not has_api:
        if raw_data.get("status") == "found" and raw_data.get("doi"):
            return "medium"
        elif raw_data.get("status") == "found":
            return "low"
        else:
            return "not_found"

    major_disagreement = any(
        d in disagreements for d in ("title", "journal", "year", "doi")
    )

    if source_agreements >= 2 and not major_disagreement:
        return "high"
    elif source_agreements == 1 and not major_disagreement:
        return "medium"
    else:
        return "low"



def merge_results(
    raw_data: dict,
    api_result: dict | None,
    raw_text: str = "",
) -> dict:
    """
    Merge LLM raw data + Crossref + Semantic Scholar results into final_data.

    Returns:
        dict with keys: ref_id, raw_text, raw_data, api_data, final_data,
                       status, field_confidence, confidence, disagreements

    field_confidence values: "raw" | "crossref" | "semantic_scholar" | "null"
    confidence values: "high" | "medium" | "low" | "not_found"
    """
    # ── Build raw_data copy with issue=null guaranteed ────────────────────────
    rd = {k: raw_data.get(k) for k in PAPER_FIELDS}
    rd["issue"] = None  # always present, API rarely provides it
    rd["type"] = raw_data.get("type")  # only from LLM

    # ── Initialise final_data and field_confidence from raw_data ───────────────
    final = {k: rd[k] for k in PAPER_FIELDS}
    final["issue"] = None
    fc: dict[str, str] = {k: ("raw" if rd.get(k) else "null") for k in PAPER_FIELDS}
    # 'type' comes only from LLM, not from API sources
    final["type"] = rd.get("type")
    fc["type"] = "raw" if rd.get("type") else "null"

    # ── Merge API data ────────────────────────────────────────────────────────
    disagreements: list[str] = []
    source_agreements = 0
    cr = api_result.get("crossref") if api_result else None
    s2 = api_result.get("semantic_scholar") if api_result else None
    has_api = bool(cr or s2)

    if not has_api:
        # No API data at all — use compute_confidence for consistent scoring
        confidence = compute_confidence(raw_data, api_result, source_agreements, disagreements)
        return _build_entry(raw_data, api_result, final, fc, raw_text,
                            confidence, disagreements)

    # Crossref is primary
    for k in PAPER_FIELDS:
        cr_v = (cr or {}).get(k)
        s2_v = (s2 or {}).get(k)
        agreed_v, is_agreed = _multi_source_agree(api_result, k)
        raw_v = rd.get(k)

        if is_agreed:
            # Sources agree — use agreed value, update source
            final[k] = agreed_v
            fc[k] = _source_for_field(raw_v, cr_v, s2_v)
            source_agreements += 1
        elif cr_v or s2_v:
            # One or both APIs have a value but they disagree
            best = _pick_best_field(api_result, k, raw_text)
            if best:
                final[k] = best
            fc[k] = _source_for_field(raw_v, cr_v, s2_v)
            if k in ("title", "journal", "year", "doi"):
                disagreements.append(k)
        # else: raw stays as-is, fc already "raw" or "null"

    # Confidence scoring (new logic — see compute_confidence docstring)
    confidence = compute_confidence(rd, api_result, source_agreements, disagreements)

    return _build_entry(raw_data, api_result, final, fc, raw_text,
                        confidence, disagreements)


def _build_entry(
    raw_data: dict,
    api_result: dict | None,
    final: dict,
    fc: dict,
    raw_text: str,
    confidence: str,
    disagreements: list[str],
) -> dict:
    status = "found" if final.get("title") else "not_found"
    ref_type = raw_data.get("type")
    return {
        "ref_id": raw_data.get("ref_id"),
        "raw_text": raw_text,
        "raw_data": {k: raw_data.get(k) for k in PAPER_FIELDS + EXTRA_FIELDS if raw_data.get(k) is not None},
        "api_data": api_result,
        "final_data": {k: v for k, v in final.items() if v is not None},
        "status": status,
        "field_confidence": fc,
        "confidence": confidence,
        "disagreements": disagreements,
    }


def extract_title_from_raw(raw_text: str) -> str | None:
    """Try to extract a paper title from raw reference text."""
    if not raw_text:
        return None
    m = re.search(r'["\u201c]([^"\u201d]+)["\u201d]', raw_text)
    if m:
        return m.group(1).strip()
    known_journal_starts = [
        "Phys.", "Science", "Opt.", "IEEE", "Nat.", "Light", "Chaos",
        "Appl.", "Rev.", "Laser", "J. ", "Acta", "Chin.", "Semiconductor",
        "Results", "Int.", "Math.", "Am.", "Nat", "Eur.", "ACS",
        "Photon", "Proc", "Advances",
    ]
    j_pos = len(raw_text)
    for j_start in known_journal_starts:
        pos = raw_text.find(j_start)
        if pos != -1 and pos < j_pos:
            j_pos = pos
    before_journal = raw_text[:j_pos].strip()
    before_journal = re.sub(r"^\[\d+\](?:\.\s*|\s+)", "", before_journal)
    before_journal = re.sub(r"^\d+\.\s*", "", before_journal)
    before_journal = re.sub(r"^\d+\s+", "", before_journal)
    before_journal = re.sub(r"^[^.]+\.\s*", "", before_journal, count=1)
    before_journal = re.sub(r"et\s+al[.,]\s*", "", before_journal, count=1)
    title = before_journal.strip().rstrip(". ").strip()
    if title and len(title) >= 5 and title[0].isupper():
        return title
    return None
