#!/usr/bin/env python3
"""
src/modules/search.py — 4-step bibliographic search cascade.

Input:  data/llm_results.json
Output: data/search_results.json

  Step 1 — DOI lookup          (CR + S2, parallel, fastest/most precise)
  Step 2 — title + journal     (CR + S2, parallel, fallback when DOI absent)
  Step 3 — journal+year/vol/pages structured search (CR only, exhaustive)
  Step 4 — MCP agent fallback  (only for entries still unresolved after Step 3)

Each entry in search_results.json stores data from ALL channels separately
(crossref / semantic_scholar / mcp), plus a "strategy_used" field.

CLI:
  python -m src.modules.search
  python -m src.modules.search --no-mcp    # skip MCP fallback step
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.utils.bib_search import (
    cr_query_by_doi, cr_query_by_title,
    s2_query_by_doi, s2_query_by_title,
    cr_structured_search, title_similarity,
)
from src.utils.journals import journals_match

# ── Config ─────────────────────────────────────────────────────────────────────

IN_JSON  = "data/02_llm/llm_results.json"
OUT_JSON = "data/03_search/search_results.json"
TMP_OUT  = "data/03_search/.search_results_tmp.json"

WORKERS = 3          # ThreadPoolExecutor workers
CR_RATE_SLEEP = 0.34  # seconds between CR requests (~3 req/s)
TITLE_SIM_THRESHOLD = 0.30  # reject CR result if token Jaccard similarity < this

# ── Per-entry search cascade ───────────────────────────────────────────────────

def _cr_throttle():
    """Throttle: sleep CR_RATE_SLEEP before each Crossref call."""
    time.sleep(CR_RATE_SLEEP)


def search_one(llm_entry: dict, use_mcp: bool = True) -> dict:
    """
    Run the 4-step cascade for a single reference.

    Returns:
        {
          "ref_id": int,
          "crossref": dict | None,
          "semantic_scholar": dict | None,
          "mcp": dict | None,
          "strategy_used": str,   # "doi" | "title_journal" | "journal_structured" | "mcp_fallback" | "not_found"
          "llm_data": dict,       # echo back LLM-parsed structured fields for downstream use
        }
    """
    rid = int(llm_entry["ref_id"])
    doi = llm_entry.get("doi") or ""
    title = llm_entry.get("title") or ""
    journal = llm_entry.get("journal") or ""
    year = llm_entry.get("year") or ""
    volume = llm_entry.get("volume") or ""
    pages = llm_entry.get("pages") or ""

    cr = None
    s2 = None
    mcp = None
    strategy = "not_found"

    # ── Step 1: DOI lookup (CR + S2, parallel within this step) ──────────────
    if doi:
        _cr_throttle()
        s2 = s2_query_by_doi(doi)
        cr = cr_query_by_doi(doi)
        if cr or s2:
            strategy = "doi"
            return _make_result(rid, cr, s2, None, strategy, llm_entry)

    # ── Step 2: title + journal ───────────────────────────────────────────────
    if title:
        _cr_throttle()
        s2 = s2_query_by_title(title, year) if not s2 else s2
        cr_raw = cr_query_by_title(title, year) if not cr else cr
        # Validate CR result: title sim must meet threshold AND journal must match.
        # journals_match() handles abbreviation↔full-name mismatches via JOURNAL_FULL.
        if cr_raw:
            rt = cr_raw.get("title") or ""
            rj = cr_raw.get("journal") or ""
            sim = title_similarity(title, rt)
            journal_ok = journals_match(journal, rj)
            if sim < TITLE_SIM_THRESHOLD or not journal_ok:
                cr_raw = None
        if cr_raw:
            cr = cr_raw
        if cr or s2:
            strategy = "title_journal"
            return _make_result(rid, cr, s2, None, strategy, llm_entry)

    # ── Step 3: journal + year/vol/pages structured search (CR only) ─────────
    if journal or volume or pages:
        cr = cr_structured_search(llm_entry)
        if cr:
            rt = cr.get("title") or ""
            rj = cr.get("journal") or ""
            sim = title_similarity(title, rt) if title else 1.0
            journal_ok = journals_match(journal, rj)
            if sim < TITLE_SIM_THRESHOLD or not journal_ok:
                cr = None
        if cr:
            strategy = "journal_structured"
            return _make_result(rid, cr, None, None, strategy, llm_entry)

    # ── Step 4: MCP agent fallback ───────────────────────────────────────────
    if use_mcp:
        try:
            mcp = _mcp_search(llm_entry)
        except Exception as e:
            print(f"  [MCP] ref {rid} error: {e}")
            mcp = None
        if mcp:
            strategy = "mcp_fallback"
            return _make_result(rid, None, None, mcp, strategy, llm_entry)

    return _make_result(rid, None, None, None, strategy, llm_entry)


def _make_result(ref_id, cr, s2, mcp, strategy, llm_entry) -> dict:
    return {
        "ref_id": ref_id,
        "crossref": cr,
        "semantic_scholar": s2,
        "mcp": mcp,
        "strategy_used": strategy,
        "llm_data": {k: llm_entry.get(k) for k in [
            "authors", "title", "journal", "year", "volume", "issue", "pages", "doi", "type"
        ] if llm_entry.get(k) is not None},
    }


# ── MCP agent fallback ─────────────────────────────────────────────────────────



def _mcp_search(llm_entry: dict) -> dict | None:
    """Call MiniMax MCP web_search for paper metadata.

    Uses the 'mcp' Python package to invoke the MiniMax MCP server via stdio.
    Strategy: search → extract DOI from results → query Crossref for structured data.
    Falls back to None gracefully on any error.

    Requires: mcp Python package (`pip install mcp`).
    Can be disabled by setting DISABLE_MCP=1 in config.env.
    """
    from src.config import get
    if get("DISABLE_MCP", "").lower() in ("1", "true", "yes"):
        return None

    try:
        import asyncio, re, json
        from urllib.parse import urlparse
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters
    except ImportError:
        return None

    # Prefer explicit MINIMAX_API_KEY; fall back to DASHSCOPE_API_KEY if not set
    key = get("MINIMAX_API_KEY", "") or get("DASHSCOPE_API_KEY", "")
    if not key:
        return None

    title = llm_entry.get("title") or ""
    journal = llm_entry.get("journal") or ""
    year = llm_entry.get("year") or ""
    authors = llm_entry.get("authors") or ""

    # Build search query
    parts = [title]
    if journal:
        parts.append(journal)
    if year:
        parts.append(year)
    query = " ".join(parts)[:200]

    async def _do_search():
        server = StdioServerParameters(
            command="uvx",
            args=["minimax-coding-plan-mcp", "-y", "-"],
            env={
                "MINIMAX_API_KEY": key,
                "MINIMAX_API_HOST": "https://api.minimaxi.com",
            },
        )
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("web_search", {"query": query})
                return result.content[0].text

    try:
        raw = asyncio.run(_do_search())
    except Exception:
        return None

    # Parse JSON response
    try:
        data = json.loads(raw)
    except Exception:
        return None

    organic = data.get("organic") if isinstance(data, dict) else None
    if not organic:
        return None

    # Extract DOI, URL, and structured metadata from all organic results
    doi_pattern = re.compile(r"10\.\d+/[^\s\"<>]+")
    # Trusted publisher domains (prefer URLs from these sources)
    TRUSTED_DOMAINS = {
        "nature.com", "springer.com", "springerlink.com", "wiley.com",
        "sciencedirect.com", "elsevier.com", "ieee.org", "ieeexplore.ieee.org",
        "osa.org", "osapublishing.org", "opg.org", "optica.org",
        "aip.org", "scitation.org", "aps.org", "arxiv.org",
        "mdpi.com", "frontiersin.org", "plos.org", "tandfonline.com",
        "sagepub.com", "oxfordjournals.org", "cambridge.org",
        "spiedigitallibrary.org", "iopscience.org", "iop.org",
        "academic.oup.com", "rupress.org", "jstor.org",
    }
    # Domains that are document-sharing / secondary aggregators (not acceptable as source_url)
    BAD_DOMAINS = {
        "doc88.com", "docin.com", "baidu.com", "sina.com", "163.com",
        "aliyun.com", "ctex.org", "cnki.net", "wanfangdata.com",
        "dspace.org", "arxiv.org",  # arxiv is ok but handled separately below
        "medsci.cn", "medpeer.cn", "mol.nuclearpotential.org",
    }
    doi_found = None
    source_url = None
    best_snippet = ""
    best_vol = None
    best_issue = None
    best_pages = None

    # Patterns for structured metadata in snippets
    vol_pat = re.compile(r"\b(?:vol(?:ume)?\.?\s*)?(\d+)\s*[,:]\s*(?:no\.?\s*)?(\d+)", re.I)
    pages_pat = re.compile(r"\b(\d+)\s*[-–]\s*(\d+)\b")

    for item in organic:
        snippet = item.get("snippet", "")
        text = f"{item.get('title', '')} {snippet}"

        # DOI
        if doi_found is None:
            m = doi_pattern.search(text)
            if m:
                doi_found = m.group(0).rstrip(".,;")

        # URL — pick the first URL from a trusted domain (skip bad domains)
        if source_url is None:
            for link_field in ("link", "url", "redirect_link"):
                lnk = item.get(link_field)
                if not (lnk and isinstance(lnk, str) and lnk.startswith("http")):
                    continue
                # Extract domain from URL
                from urllib.parse import urlparse
                try:
                    domain = urlparse(lnk).netloc.lower().removeprefix("www.")
                except Exception:
                    continue
                if domain in BAD_DOMAINS:
                    continue
                # arxiv.org is ok
                if domain == "arxiv.org" or domain.endswith(".arxiv.org"):
                    source_url = lnk
                    break
                if any(domain == td or domain.endswith(f".{td}") for td in TRUSTED_DOMAINS):
                    source_url = lnk
                    break

        # Keep best snippet (longest)
        if len(snippet) > len(best_snippet):
            best_snippet = snippet
            vm = vol_pat.search(snippet)
            if vm:
                best_vol = vm.group(1)
                best_issue = vm.group(2)
            pm = pages_pat.search(snippet)
            if pm:
                best_pages = f"{pm.group(1)}-{pm.group(2)}"

    # If DOI found, query Crossref for structured data
    if doi_found:
        cr = cr_query_by_doi(doi_found)
        if cr:
            cr["source_url"] = source_url
            return cr
        # DOI not in CR — return basic extracted data
        result = {
            "title": title,
            "authors": authors,
            "journal": journal,
            "year": year,
            "doi": doi_found,
            "source_url": source_url,
        }
        # Fill gaps from MCP snippet parsing (only vol/issue/pages; keep LLM journal/year)
        if best_vol:
            result["volume"] = best_vol
        if best_issue:
            result["issue"] = best_issue
        if best_pages:
            result["pages"] = best_pages
        return result

    # No DOI — source_url may still be present from a trusted domain
    return {
        "title": title,
        "authors": authors,
        "journal": journal,
        "year": year,
        "source_url": source_url,
        "snippet": best_snippet[:200],
    }



# ── Parallel dispatch ──────────────────────────────────────────────────────────

def _dispatch_search(llm_entries: list[dict], use_mcp: bool = True) -> list[dict]:
    """Run search cascade for all entries with ThreadPoolExecutor rate limiting."""
    project_root = Path(__file__).parent.parent.parent
    tmp_path = project_root / TMP_OUT
    results = {}
    done = 0

    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(llm_entries), desc="  Search", unit="ref", ncols=80)
        _pbar_write = pbar.write  # use tqdm.write() instead of print() to avoid interleaving
    except ImportError:
        pbar = None
        _pbar_write = print

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(search_one, e, use_mcp): e for e in llm_entries}
        for future in as_completed(futures):
            entry = futures[future]
            rid = entry["ref_id"]
            try:
                result = future.result()
            except Exception as exc:
                _pbar_write(f"  [{rid}] search error: {exc}")
                result = _make_result(rid, None, None, None, "not_found", entry)
            results[rid] = result
            done += 1

            if pbar:
                pbar.update(1)
                pbar.set_postfix_str(f"[{rid}] {result['strategy_used']}")
            else:
                sym = {"doi": "✓D", "title_journal": "✓T", "journal_structured": "✓S",
                       "mcp_fallback": "✓M", "not_found": "✗"}
                _pbar_write(f"  [{rid}] {sym.get(result['strategy_used'], '?')}  {result['strategy_used']}")

            # Stream-save every 20 entries
            if done % 20 == 0 or done == len(llm_entries):
                _stream_save(
                    [results.get(int(e["ref_id"]), _make_result(int(e["ref_id"]), None, None, None, "not_found", e))
                     for e in llm_entries],
                    tmp_path
                )

    if pbar:
        pbar.close()

    return [results.get(int(e["ref_id"]), _make_result(int(e["ref_id"]), None, None, None, "not_found", e))
            for e in llm_entries]


def _stream_save(results: list, path: Path):
    path.parent.mkdir(exist_ok=True, parents=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    project_root = Path(__file__).parent.parent.parent
    llm_path = project_root / IN_JSON
    out_path = project_root / OUT_JSON
    tmp_path = project_root / TMP_OUT

    # Check for --no-mcp flag
    use_mcp = "--no-mcp" not in sys.argv

    if not llm_path.exists():
        print(f"ERROR: {llm_path} not found — run 'python -m src.modules.llm_parse' first.")
        sys.exit(1)

    with open(llm_path, encoding="utf-8") as f:
        llm_entries = json.load(f)

    print(f"Loaded {len(llm_entries)} LLM entries from {llm_path}")
    print(f"Strategy: DOI → title+journal → journal_structured → MCP")
    if not use_mcp:
        print("  MCP fallback: DISABLED (--no-mcp)")
    print(f"Concurrency: {WORKERS} workers, ~3 CR req/s")

    results = _dispatch_search(llm_entries, use_mcp=use_mcp)
    results.sort(key=lambda x: int(x["ref_id"]))

    _stream_save(results, out_path)
    if tmp_path.exists():
        tmp_path.unlink()

    # Summary
    strategy_counts: dict[str, int] = {}
    for r in results:
        s = r["strategy_used"]
        strategy_counts[s] = strategy_counts.get(s, 0) + 1

    print(f"\n══ search_results.json → {out_path} ══")
    print(f"  Strategy breakdown:")
    for s, c in sorted(strategy_counts.items()):
        print(f"    {s}: {c}")
    has_data = sum(1 for r in results if r["crossref"] or r["semantic_scholar"] or r["mcp"])
    print(f"  Total with data: {has_data}/{len(results)}")


if __name__ == "__main__":
    main()
