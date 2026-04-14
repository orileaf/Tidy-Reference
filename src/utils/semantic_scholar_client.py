#!/usr/bin/env python3
"""
src/utils/semantic_scholar_client.py — Semantic Scholar API queries and normalization.
"""

import re
import time
from typing import Any

import requests

from src.utils.constants import RATE_LIMIT_DELAY

MAX_RETRIES = 3


def _do_get(url: str, params: dict, headers: dict) -> requests.Response:
    """GET with retry on 429 using exponential backoff."""
    for attempt in range(MAX_RETRIES):
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code == 429:
            wait = (attempt + 1) * 2.0  # 2s, 4s, 6s
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    return r  # final 429 after all retries


def _normalize_semantic_scholar(s2: dict) -> dict[str, Any]:
    """Convert Semantic Scholar item to our normalized schema."""
    authors = []
    for a in s2.get("authors", []):
        name = f"{a.get('lastName','')}, {a.get('firstName','')}"
        name = re.sub(r',\s*$', '', name).strip()
        if name and name != ",":
            authors.append(name)
    authors_str = " and ".join(authors) if authors else None

    venue = s2.get("venue") or ""
    year = str(s2.get("year")) if s2.get("year") else None

    return {
        "title": s2.get("title"),
        "authors": authors_str,
        "journal": venue if venue else None,
        "year": year,
        "volume": str(s2["volume"]) if s2.get("volume") else None,
        "issue": None,  # Semantic Scholar does not provide issue
        "pages": str(s2["pageRange"]) if s2.get("pageRange") else None,
        "doi": s2.get("externalIds", {}).get("DOI"),
    }


def query_semantic_scholar_by_doi(doi: str) -> dict | None:
    """Query Semantic Scholar by DOI. Returns normalized data or None."""
    if not doi:
        return None
    try:
        url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
        params = {"fields": "title,authors,year,venue,volume,pageRange,externalIds"}
        r = _do_get(url, params=params, headers={})
        if r.status_code == 429:
            return None
        return _normalize_semantic_scholar(r.json())
    except Exception:
        pass
    return None


def query_semantic_scholar_by_title(title: str, year: str | None = None) -> dict | None:
    """Query Semantic Scholar by title. Returns normalized data or None."""
    if not title:
        return None
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {"query": title[:200],
                  "fields": "title,authors,year,venue,volume,pageRange,externalIds",
                  "limit": 3}
        r = _do_get(url, params=params, headers={})
        if r.status_code == 429:
            return None
        items = r.json().get("data", [])
        if items:
            if year:
                for item in items:
                    if str(item.get("year") or "") == year:
                        return _normalize_semantic_scholar(item)
            return _normalize_semantic_scholar(items[0])
    except Exception:
        pass
    return None
