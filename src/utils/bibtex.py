#!/usr/bin/env python3
"""
src/utils/bibtex.py — BibTeX key generation and entry formatting.
"""

import re

from src.utils.journals import JOURNAL_ABBR, JOURNAL_FULL
from src.utils.latex import clean_latex


def abbrev_journal(j: str) -> str:
    if not j:
        return j or ""
    return JOURNAL_ABBR.get(j, j)


def expand_journal(j: str) -> str:
    if not j:
        return j or ""
    return JOURNAL_FULL.get(j, j)


def _read(entry: dict, key: str):
    """Read a field: prefer final_data, fall back to top-level entry."""
    if key == "api_data":
        return entry.get(key, {})
    if key == "disagreements":
        return entry.get(key, [])
    if key == "confidence":
        return entry.get(key, "unknown")
    if key == "ref_id":
        return entry.get(key, "?")
    # Standard paper fields: try final_data first, then top-level
    src = entry.get("final_data", entry)
    return src.get(key)


def make_bibkey(r: dict, used_keys: dict) -> str:
    """Generate a BibTeX cite key: FirstAuthorLastYear."""
    authors_str = _read(r, "authors") or ""
    if not authors_str.strip():
        api_data = _read(r, "api_data") or {}
        authors_str = api_data.get("authors") or ""
    authors_str = authors_str.replace(";", " and ")
    first_author = re.split(r"\s+and\s+", authors_str)[0].strip()
    parts = first_author.split(",")
    if parts and parts[0].strip():
        last_name = parts[0].strip()
    elif first_author.split():
        last_name = first_author.split()[-1]
    else:
        last_name = "Unknown"
    last_name = re.sub(r"[^a-zA-Z]", "", last_name) or "Unknown"
    year = str(_read(r, "year") or "nd")
    key = f"{last_name}{year}"
    if key in used_keys:
        used_keys[key] += 1
        key = f"{key}{used_keys[key]}"
    else:
        used_keys[key] = 1
    return key


def bibtex_entry(r: dict, used_keys: dict) -> str:
    """Generate a BibTeX @article entry string."""
    key = make_bibkey(r, used_keys)
    api_data = _read(r, "api_data") or {}

    # When confidence is low, api_data may contain hallucinated fields from wrong DOI lookup.
    disagreement_fields = _read(r, "disagreements") or []
    safe_api = dict(api_data)
    if any(f in disagreement_fields for f in ("journal_doi_mismatch", "title", "doi")):
        safe_api["title"] = None
        safe_api["doi"] = None
    if any(f in disagreement_fields for f in ("journal_doi_mismatch", "journal")):
        safe_api["journal"] = None

    src = r.get("final_data", r)  # final_data if present, else top-level

    authors_str = (src.get("authors") or safe_api.get("authors") or "").replace(";", " and ")
    authors = clean_latex(authors_str)
    title = clean_latex(src.get("title") or safe_api.get("title"))
    journal_full = src.get("journal") or safe_api.get("journal") or ""
    journal = clean_latex(abbrev_journal(journal_full)) or journal_full
    year = str(src.get("year") or safe_api.get("year") or "")
    volume = src.get("volume") or safe_api.get("volume") or ""
    issue = src.get("issue") or safe_api.get("issue") or ""
    pages = src.get("pages") or safe_api.get("pages") or ""
    doi = src.get("doi") or safe_api.get("doi") or ""
    confidence = _read(r, "confidence")
    ref_id = _read(r, "ref_id")

    fields = []
    def f(k, v):
        if v:
            fields.append(f"  {k} = {{{v}}},")
    f("author", authors)
    f("title", title)
    f("journal", journal)
    f("year", year)
    f("volume", volume)
    if issue:
        f("number", issue)
    if pages:
        f("pages", pages)
    if doi:
        f("doi", doi)
        f("url", f"https://doi.org/{doi}")

    # Use type from LLM (article, inproceedings, book, incollection), default article
    bibtype = src.get("type") or "article"
    if bibtype not in ("article", "inproceedings", "book", "incollection", "thesis", "misc"):
        bibtype = "article"

    entry_str = f"@{bibtype}{{{key},\n" + "\n".join(fields)
    entry_str += f"\n  % confidence: {confidence}  |  ref #{ref_id}\n}}\n"
    return entry_str
