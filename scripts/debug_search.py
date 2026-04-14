#!/usr/bin/env python3
"""Debug search on first 20 entries — print per-step results."""
import sys, json, time, re
from pathlib import Path
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.bib_search import (
    cr_query_by_doi, s2_query_by_doi,
    cr_query_by_title, s2_query_by_title,
    cr_structured_search, title_similarity,
)
from src.utils.journals import journals_match

IN_JSON = PROJECT_ROOT / "data/llm_results.json"
WORKERS = 3
TITLE_SIM_THRESHOLD = 0.30
CR_RATE_SLEEP = 0.34

def _cr_throttle():
    time.sleep(CR_RATE_SLEEP)

def test_one(llm_entry: dict) -> dict:
    rid = int(llm_entry["ref_id"])
    doi   = llm_entry.get("doi") or ""
    title = llm_entry.get("title") or ""
    journal = llm_entry.get("journal") or ""
    year   = llm_entry.get("year") or ""
    volume = llm_entry.get("volume") or ""
    pages  = llm_entry.get("pages") or ""
    authors = llm_entry.get("authors") or ""

    cr = s2_cached = None

    # Step 1: DOI
    if doi:
        _cr_throttle()
        cr = cr_query_by_doi(doi)
        s2_cached = s2_query_by_doi(doi)
        if cr or s2_cached:
            return {"rid": rid, "step": 1, "doi": doi, "cr": bool(cr), "s2": bool(s2_cached)}

    # Step 2: title + journal
    if title:
        _cr_throttle()
        cr_raw = cr_query_by_title(title, year) if not cr else cr
        s2_cached = s2_query_by_title(title, year) if not s2_cached else s2_cached

        if cr_raw:
            rt = cr_raw.get("title") or ""
            rj = cr_raw.get("journal") or ""
            sim = title_similarity(title, rt)
            journal_ok = journals_match(journal, rj)
            if sim < TITLE_SIM_THRESHOLD or not journal_ok:
                cr_raw = None
                reason = f"FAIL: sim={sim:.2f}({'OK' if sim>=0.3 else 'LOW'}≥0.3) journal={'OK' if journal_ok else 'MISMATCH'}"
            else:
                reason = f"PASS: sim={sim:.2f} journal={'OK' if journal_ok else 'MISMATCH'}"
        else:
            reason = "FAIL: cr returned nothing"

        return {
            "rid": rid, "step": 2,
            "title": title[:60],
            "journal": journal, "year": year,
            "cr_found": bool(cr_raw),
            "s2_found": bool(s2_cached),
            "reason": reason,
            "cr_title": (cr_raw or {}).get("title", "")[:60] if cr_raw else "(no result)",
            "s2_title": (s2_cached or {}).get("title", "")[:60] if s2_cached else "(no result)",
            "s2_journal": (s2_cached or {}).get("journal", "") if s2_cached else None,
        }

    # Step 3: journal structured
    if journal or volume or pages:
        cr = cr_structured_search(llm_entry)
        if cr:
            rt = cr.get("title") or ""
            rj = cr.get("journal") or ""
            sim = title_similarity(title, rt) if title else 1.0
            journal_ok = journals_match(journal, rj)
            if sim < TITLE_SIM_THRESHOLD or not journal_ok:
                cr = None
                reason = f"FAIL: sim={sim:.2f} journal={'OK' if journal_ok else 'MISMATCH'}"
            else:
                reason = f"PASS: sim={sim:.2f}"
        else:
            reason = "FAIL: cr_structured returned nothing"
        return {"rid": rid, "step": 3, "cr_found": bool(cr), "reason": reason,
                "cr_title": (cr or {}).get("title", "")[:60] if cr else "(no result)"}

    return {"rid": rid, "step": 0, "title": title[:60], "reason": "NO DATA (no title/journal/page_info)"}


def main():
    with open(IN_JSON) as f:
        entries = json.load(f)

    first20 = entries[:20]
    print(f"\n{'─'*80}")
    print(f"Testing first {len(first20)} entries")
    print(f"{'─'*80}")

    for e in first20:
        r = test_one(e)
        print()
        print(f"  [{r['rid']}] Step {r['step']}")
        if "title" in r:
            print(f"    query title : {r.get('title','')}")
            print(f"    query journal: {r.get('journal','')}")
            print(f"    query year  : {r.get('year','')}")
        if r.get("cr_found") is not None:
            print(f"    CR found    : {r.get('cr_found')} | S2 found: {r.get('s2_found', r.get('cr_found'))}")
            print(f"    reason      : {r.get('reason','')}")
            if r.get("cr_title"):
                print(f"    CR title    : {r['cr_title']}")
            if r.get("s2_title"):
                print(f"    S2 title    : {r['s2_title']}")
                print(f"    S2 journal  : {r.get('s2_journal','')}")
        if r.get("reason"):
            print(f"    reason      : {r.get('reason','')}")


if __name__ == "__main__":
    main()
