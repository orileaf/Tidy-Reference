#!/usr/bin/env python3
"""
src/utils/bib_search.py — Shared bibliographic search helpers.
Used by stage2b_api.py (step1) and stage2c_retry.py (step2).
"""

import re
import time
import requests

from src.config import get
from src.utils.journals import journals_match

# Crossref polite pool: ~3 req/s.  Concurrency 3 + 0.34s sleep ≈ 3 req/s.
CR_CONCURRENCY = 3
CR_RATE_SLEEP = 0.34   # seconds between semaphore acquisitions (3 req/s)
_session = None
_sem = None


def _get_session() -> requests.Session:
    """Return a shared requests.Session (connection pooling)."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": "References-Lookup/1.0"})
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=2,
        )
        _session.mount("https://api.crossref.org", adapter)
        _session.mount("https://api.semanticscholar.org", adapter)
    return _session


def _cr_throttle():
    """Rate-limit Crossref calls to ~3 req/s across all threads."""
    global _sem
    if _sem is None:
        _sem = __import__("threading").Semaphore(CR_CONCURRENCY)
    _sem.acquire()
    try:
        time.sleep(CR_RATE_SLEEP)
    finally:
        _sem.release()


# ─── Title similarity ───────────────────────────────────────────────────────────

_STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "with", "to", "by", "and", "or", "using", "based", "from"}

def title_similarity(query_title: str, result_title: str) -> float:
    """
    Compute normalized token Jaccard similarity between two titles.
    Returns a float in [0.0, 1.0].
    """
    def _tokens(t: str) -> set[str]:
        return {
            re.sub(r"[^a-z0-9]", "", w.lower())
            for w in re.split(r"[\s\-:;,.()]+", t)
            if len(w) > 2 and w.lower() not in _STOPWORDS
        }
    q_tokens = _tokens(query_title)
    r_tokens = _tokens(result_title)
    if not q_tokens:
        return 0.0
    inter = len(q_tokens & r_tokens)
    union = len(q_tokens | r_tokens)
    return inter / union if union else 0.0


# ─── Scoring ────────────────────────────────────────────────────────────────────

def score_result(cr: dict, llm: dict) -> int:
    """
    Score a Crossref result against LLM-parsed structured fields.
    Higher score = better match.  Returns 0 if clearly wrong.
    Accepted only when score >= 6.
    """
    score = 0

    # Journal — journals_match() handles abbreviation↔full-name comparison
    if journals_match(llm.get("journal") or "", cr.get("journal") or ""):
        score += 3

    if llm.get("year") and cr.get("year") and llm["year"] == cr["year"]:
        score += 1.1

    if llm.get("volume") and cr.get("volume") and llm["volume"] == cr["volume"]:
        score += 1.2

    llm_pg = llm.get("pages") or ""
    cr_pg = cr.get("pages") or ""
    if llm_pg and cr_pg and (llm_pg in cr_pg or cr_pg in llm_pg):
        score += 1.4

    llm_auth = (llm.get("authors") or "").lower()
    cr_auth = (cr.get("authors") or "").lower()
    if llm_auth and cr_auth:
        def _surnames(s: str):
            return {
                re.sub(r"[^a-z]", "", p.split(",")[0].strip())
                for p in re.split(r"\s+and\s+", s)
                if p.strip()
            }

        raw_set = _surnames(llm_auth)
        cr_set = _surnames(cr_auth)
        if raw_set and cr_set:
            overlap = len(raw_set & cr_set)
            thr = len(raw_set) if len(raw_set) <= 3 else (len(raw_set) + 1) // 2
            if overlap >= thr:
                score += 3

    return score


# ─── Crossref API calls ─────────────────────────────────────────────────────────

def cr_query_by_doi(doi: str) -> dict | None:
    """Query Crossref by DOI. Returns normalized data or None."""
    if not doi:
        return None
    _cr_throttle()
    try:
        session = _get_session()
        mailto = get("CROSSREF_MAILTO") or "refs@example.com"
        r = session.get(
            f"https://api.crossref.org/works/{doi}",
            headers={"User-Agent": f"References-Lookup/1.0 (mailto:{mailto})"},
            timeout=15,
        )
        r.raise_for_status()
        from src.utils.crossref_client import _normalize_crossref
        return _normalize_crossref(r.json().get("message", {}))
    except Exception:
        return None


def cr_query_by_title(title: str, year: str | None = None) -> dict | None:
    """Search Crossref by title. Returns first result or None."""
    if not title:
        return None
    params = {"query.title": title[:200], "rows": 3}
    if year:
        params["filter"] = f"from-pub-date:{year},until-pub-date:{year}"
    _cr_throttle()
    try:
        session = _get_session()
        mailto = get("CROSSREF_MAILTO") or "refs@example.com"
        r = session.get(
            "https://api.crossref.org/works",
            params=params,
            headers={"User-Agent": f"References-Lookup/1.0 (mailto:{mailto})"},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        if items:
            from src.utils.crossref_client import _normalize_crossref
            return _normalize_crossref(items[0])
    except Exception:
        return None


def cr_structured_search(llm: dict) -> dict | None:
    """
    Query Crossref using all available LLM-parsed structured fields
    (author surname + journal + year + volume + pages), score results, return best.
    Always returns the best result (score filtering is done by the caller/merge stage).
    """
    first_author = llm.get("authors") or ""
    year = llm.get("year") or ""
    journal = llm.get("journal") or ""
    volume = llm.get("volume") or ""
    pages = llm.get("pages") or ""

    parts = re.split(r"\s+and\s+", first_author)
    first_block = parts[0].strip() if parts else ""
    surname = first_block.split(",")[0].strip() if first_block else ""

    if not surname and not year:
        return None

    bib_parts = [p for p in [journal, year, f"vol. {volume}", f"p. {pages}"] if p]
    bib_query = " ".join(bib_parts)

    params = {"rows": 5}
    if surname:
        params["query.author"] = surname
    if bib_query:
        params["query.bibliographic"] = bib_query
    if year:
        params["filter"] = f"from-pub-date:{year},until-pub-date:{year}"

    _cr_throttle()
    try:
        session = _get_session()
        mailto = get("CROSSREF_MAILTO") or "refs@example.com"
        r = session.get(
            "https://api.crossref.org/works",
            params=params,
            headers={"User-Agent": f"References-Lookup/1.0 (mailto:{mailto})"},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        best_result, best_score = None, 0
        for item in items:
            from src.utils.crossref_client import _normalize_crossref
            cr = _normalize_crossref(item)
            s = score_result(cr, llm)
            if s > best_score:
                best_score = s
                best_result = cr
        return best_result  # always return best match; scoring is done by the caller
    except Exception:
        return None


# ─── Semantic Scholar API calls ─────────────────────────────────────────────────

def s2_query_by_doi(doi: str) -> dict | None:
    """Query Semantic Scholar by DOI."""
    if not doi:
        return None
    try:
        session = _get_session()
        api_key = get("SEMANTIC_SCHOLAR_API_KEY") or ""
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        r = session.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
            "?fields=title,authors,year,venue,volume,pageRange,externalIds",
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        from src.utils.semantic_scholar_client import _normalize_semantic_scholar
        return _normalize_semantic_scholar(r.json())
    except Exception:
        return None


def s2_query_by_title(title: str, year: str | None = None) -> dict | None:
    """Search Semantic Scholar by title."""
    if not title:
        return None
    try:
        session = _get_session()
        api_key = get("SEMANTIC_SCHOLAR_API_KEY") or ""
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        r = session.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": title, "fields": "title,authors,year,venue,volume,pageRange,externalIds", "limit": 3},
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        from src.utils.semantic_scholar_client import _normalize_semantic_scholar
        items = r.json().get("data", [])
        if not items:
            return None
        if year:
            for item in items:
                if str(item.get("year") or "") == year:
                    return _normalize_semantic_scholar(item)
        return _normalize_semantic_scholar(items[0])
    except Exception:
        return None
