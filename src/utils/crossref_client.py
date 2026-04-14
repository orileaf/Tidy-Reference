#!/usr/bin/env python3
"""
src/utils/crossref_client.py — Crossref API queries and normalization.
"""

import re
import time
from typing import Any

import requests

from src.config import get
from src.utils.constants import RATE_LIMIT_DELAY


def _crossref_headers() -> dict[str, str]:
    mailto = get("CROSSREF_MAILTO") or "refs@example.com"
    return {"User-Agent": f"References-Lookup/1.0 (mailto:{mailto})"}


def _extract_year(cr: dict) -> str | None:
    for date_key in ["published-print", "published-online", "created"]:
        date_obj = cr.get(date_key) or {}
        parts = date_obj.get("date-parts", [[]])
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return None


def _normalize_crossref(cr: dict) -> dict[str, Any]:
    """Convert Crossref item to our normalized schema."""
    authors = []
    for a in cr.get("author", []):
        name = f"{a.get('family','')}, {a.get('given','')}"
        name = re.sub(r',\s*$', '', name)
        authors.append(name)
    authors_str = " and ".join(authors) if authors else None

    volume = None
    v_list = cr.get("volume")
    if isinstance(v_list, list) and v_list:
        volume = str(v_list[0])
    elif isinstance(v_list, str) and v_list:
        volume = v_list

    pages_val = str(cr["page"]) if cr.get("page") else None

    return {
        "title": cr.get("title", [None])[0] if cr.get("title") else None,
        "authors": authors_str,
        "journal": cr.get("container-title", [None])[0] if cr.get("container-title") else None,
        "year": _extract_year(cr),
        "volume": volume,
        "issue": str(cr["issue"]) if cr.get("issue") else None,
        "pages": pages_val,
        "doi": cr.get("DOI"),
        "publisher": cr.get("publisher"),
        "location": (
            cr.get("institution", [{}])[0].get("location")
            if cr.get("institution")
            else cr.get("publisher-location")
        ),
    }


def query_crossref_by_title(title: str, year: str | None = None) -> dict | None:
    """Search Crossref by title. Returns first result or None."""
    if not title:
        return None
    params = {"query.title": title[:200], "rows": 3}
    if year:
        params["filter"] = f"from-pub-date:{year},until-pub-date:{year}"
    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get("https://api.crossref.org/works", params=params,
                          headers=_crossref_headers(), timeout=15)
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        if items:
            return _normalize_crossref(items[0])
    except Exception:
        pass
    return None


def query_crossref_by_doi(doi: str) -> dict | None:
    """Query Crossref by DOI. Returns normalized data or None."""
    if not doi:
        return None
    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get(f"https://api.crossref.org/works/{doi}",
                          headers=_crossref_headers(), timeout=15)
        r.raise_for_status()
        return _normalize_crossref(r.json().get("message", {}))
    except Exception:
        pass
    return None
