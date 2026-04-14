#!/usr/bin/env python3
"""
src/utils/formatters.py — IEEE, Nature, and GB/T 7714-2015 formatted reference output.
"""

import html
import re

from src.utils.bibtex import expand_journal


def _decode(text: str) -> str:
    """Decode XML/HTML entities: &amp; → &, &lt; → <, etc."""
    return html.unescape(text) if text else text


def _safe_api_data(entry: dict) -> dict:
    """Return api_data with fields nulled if marked as disagreements.

    Handles both old format (journal_doi_mismatch) and new format
    (individual field names like "title", "journal", "doi").
    """
    api = dict(entry.get("api_data") or {})
    for f in entry.get("disagreements") or []:
        if f in ("journal_doi_mismatch", "title", "doi"):
            api["title"] = None
            api["doi"] = None
        if f in ("journal_doi_mismatch", "journal"):
            api["journal"] = None
    return api


def format_authors_display(authors_str: str | None, style: str = "ieee") -> str:
    """Format BibTeX author string for display."""
    if not authors_str:
        return ""
    authors_str = authors_str.replace(";", " and ")
    authors = re.split(r"\s+and\s+", authors_str)
    parts = []
    for a in authors:
        a = a.strip()
        if not a:
            continue
        if "," in a:
            last, first = a.split(",", 1)
            initials = " ".join(w[0] + "." for w in first.strip().split() if w)
            parts.append(f"{initials} {last.strip()}".strip())
        else:
            parts.append(a)
    if style == "ieee":
        if len(parts) > 3:
            return ", ".join(parts[:3]) + ", et al."
        return ", ".join(parts) if len(parts) > 1 else (parts[0] if parts else "")
    else:
        if len(parts) > 5:
            return ", ".join(parts[:5]) + " et al."
        return ", ".join(parts) if len(parts) > 1 else (parts[0] if parts else "")


def format_ieee(entry: dict, ref_num: int) -> str:
    safe_api = _safe_api_data(entry)
    src = entry.get("final_data", entry)  # prefer final_data, fall back to top-level
    authors = format_authors_display(src.get("authors") or safe_api.get("authors"))
    title = src.get("title") or safe_api.get("title") or "Untitled"
    journal = expand_journal(_decode(src.get("journal") or safe_api.get("journal") or ""))
    volume = src.get("volume") or safe_api.get("volume") or ""
    pages = src.get("pages") or safe_api.get("pages") or ""
    year = str(src.get("year") or safe_api.get("year") or "n.d.")

    chunks = [f"[{ref_num}]"]
    if authors:
        chunks.append(f"{authors},")
    chunks.append(f'"{title},"')
    if journal:
        chunks.append(f"{journal},")
    if volume:
        chunks.append(f"vol. {volume},")
    if pages:
        chunks.append(f"pp. {pages},")
    chunks.append(f"{year}.")
    return " ".join(chunks)


def format_nature(entry: dict, ref_num: int) -> str:
    safe_api = _safe_api_data(entry)
    src = entry.get("final_data", entry)
    authors = format_authors_display(src.get("authors") or safe_api.get("authors"), "nature")
    title = src.get("title") or safe_api.get("title") or "Untitled"
    journal = expand_journal(_decode(src.get("journal") or safe_api.get("journal") or ""))
    volume = src.get("volume") or safe_api.get("volume") or ""
    pages = src.get("pages") or safe_api.get("pages") or ""
    year = str(src.get("year") or safe_api.get("year") or "")

    chunks = []
    if authors:
        chunks.append(f"{authors}.")
    chunks.append(f"{title}.")
    if journal:
        vol_part = f"{journal} {volume}" if volume else journal
        if pages:
            vol_part += f", {pages}"
        chunks.append(vol_part)
    if year:
        chunks.append(f"({year})")
    return " ".join(chunks)


# ─── GB/T 7714-2015 formatter ───────────────────────────────────────────────────

def _initials_with_spaces(initials_str: str) -> str:
    """
    Extract initials with correct GB/T spacing.
    Core rule: any word with a trailing period is an initial → separate from what precedes it.
    "Gregory D." → "G D"   (period-space → two initials → space)
    "Edward N."  → "E N"   (period-space → two initials → space)
    "B. W."      → "B W"   (period-space → two initials → space)
    "F.T."       → "F T"   (two periods, no space → split by dot → space)
    "Junji"      → "J"     (no periods → first char only)
    """
    base = initials_str.rstrip(".")
    # Period-space in word sequence: split by ". " → each part is an initial
    if ". " in base:
        parts = base.split(". ")
        chars = [p.strip().rstrip(".")[0] for p in parts if p.strip().rstrip(".")]
        return " ".join(chars)
    # Multiple periods without space: split by "." → each segment is an initial
    elif ". " not in base and base.count(".") >= 1 and any("." in w for w in base.split()):
        # "F.T." → ["F", "T"] → "F T"
        segments = [s.strip() for s in base.split(".") if s.strip()]
        chars = [s[0] for s in segments if s]
        return " ".join(chars)
    # Single trailing period with no space: word-by-word processing
    else:
        words = initials_str.split()
        result = ""
        for w in words:
            if w.endswith("."):
                stripped = w.rstrip(".")
                if stripped:
                    result = (result + " " + stripped).strip()
            else:
                if w.strip():
                    result = (result + " " + w.strip()[0]).strip()
        return result


def _detect_ambiguous_author(author_str: str) -> str | None:
    """
    Detect if an author string has an ambiguous format (no comma + has period).
    Returns the raw string if problematic, None if OK.
   正常检索应提供逗号分隔的标准 BibTeX 格式，无法识别的格式记录到 report。
    """
    if "," in author_str:
        return None
    # No comma: if string has period + space + word → likely missing comma
    if re.search(r"\w+\.\s+\w", author_str):
        return author_str.strip()
    return None


def _parse_authors(authors_str: str) -> list[dict]:
    """
    Parse a BibTeX author string into structured records.
      1. "Last, First" (Western): surname=last, initials from first
      2. "First Last" (Chinese/international): surname=last, initials from first
    Ambiguous formats (no comma + period) are flagged by _detect_ambiguous_author.
    Returns [{"surname": str, "initials": str}, ...]
    """
    if not authors_str:
        return []
    authors_str = authors_str.replace(";", " and ")
    results = []
    for a in re.split(r"\s+and\s+", authors_str):
        a = a.strip()
        if not a:
            continue
        if "," in a:
            last, first = a.split(",", 1)
            last = last.strip()
            first = first.strip()
            inits = _initials_with_spaces(first)
        else:
            parts = a.split()
            if len(parts) >= 2:
                # Chinese SURNAME GivenName (e.g. "LI Jiancheng"):
                # first word all-caps → surname, rest → given name
                if parts[0].isupper():
                    last = parts[0]
                    first = " ".join(parts[1:])
                    inits = _initials_with_spaces(first)
                else:
                    first = " ".join(parts[:-1])
                    last = parts[-1]
                    inits = _initials_with_spaces(first)
            else:
                last = a
                inits = ""
        results.append({"surname": last, "initials": inits})
    return results


def _format_authors_gb(parsed: list[dict], max_list: int = 3) -> str:
    """
    Format authors in GB/T 7714-2015 style:
    - Surname in mixed case, space, initials with NO periods and spaces between
      each letter (e.g. "Smith J A, Jones J B")
    - Periods removed from initials: "C. O." → "C O", "A" → "A" (no trailing period)
    - English surnames from Crossref stored in ALL CAPS → keep as-is
    - If more than max_list authors: first author + " et al."
    """
    if not parsed:
        return ""
    parts = []
    for p in parsed:
        # initials already spaced by _parse_authors (e.g. "B W", "L Q", "EN")
        inits = p["initials"]
        if inits:
            parts.append(f"{p['surname']} {inits}")
        else:
            parts.append(p["surname"])
    if len(parts) >= max_list:
        return ", ".join(parts[:max_list]) + ", et al."
    return ", ".join(parts)


def _sentence_case(s: str) -> str:
    """Convert title to sentence case: first letter upper, rest lower."""
    if not s:
        return s
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i+1:].lower()
    return s.lower()


def format_gb(entry: dict, ref_num: int) -> str:
    """
    Format a reference entry in GB/T 7714-2015 numeric style.

    Journal article [J]  (A.8):
      [1] Smith JA, Jones JB. Title of the article[J]. Journal Name, 2020, 12(3): 45-56.
    Book [M] (A.1):
      [1] Smith JA. Title of the book[M]. Place: Publisher, 2020.
    Conference paper [C] (A.2):
      [1] Smith JA. Title of the paper[C]// Conference Name. Place: Publisher, 2020: 123-128.
    Unknown / other: no type marker, author + title + available info.
    """
    safe_api = _safe_api_data(entry)
    src = entry.get("final_data", entry)
    # Remove non-field keys that may leak from the top-level entry
    for _excluded in ("ref_id", "raw_text", "raw_data", "api_data",
                      "field_confidence", "confidence", "disagreements", "status"):
        src = {k: v for k, v in src.items() if k != _excluded}

    authors_raw = src.get("authors") or safe_api.get("authors") or ""
    parsed = _parse_authors(authors_raw)
    authors = _format_authors_gb(parsed)
    title = src.get("title") or safe_api.get("title") or ""
    journal = _decode(src.get("journal") or safe_api.get("journal") or "")
    volume = src.get("volume") or safe_api.get("volume") or ""
    issue = src.get("issue") or safe_api.get("issue") or ""
    pages = src.get("pages") or safe_api.get("pages") or ""
    year = str(src.get("year") or safe_api.get("year") or "")
    ref_type = (src.get("type") or "unknown").lower()

    parts = [f"[{ref_num}]"]
    if authors:
        parts.append(f"{authors.rstrip('.')}.")

    # ── Journal article [J] ────────────────────────────────────────────────────
    if ref_type == "article" or (ref_type == "unknown" and journal and not title):
        if title:
            parts.append(f"{_sentence_case(title)}[J].")
        if journal:
            # Format: Journal Name, Year, Volume(Issue): Pages
            seg = journal.rstrip(".，, ")
            if year:
                seg += f", {year}"
            if volume:
                seg += f", {volume}"
                if issue:
                    seg += f"({issue})"
            if pages:
                seg += f": {pages}"
            parts.append(f"{seg}.")
        elif year:
            parts.append(f"{year}.")

    # ── Book [M] ─────────────────────────────────────────────────────────────
    elif ref_type == "book":
        # Crossref books store series as journal — promote to title if no title
        if not title and journal:
            title = journal
        if title:
            seg = f"{title}[M]"
            edition = src.get("edition") or safe_api.get("edition") or ""
            if edition:
                # Strip trailing period first to avoid "3rd ed.. Berlin"
                seg += ". " + edition.rstrip(".")
            location = src.get("location") or safe_api.get("location") or ""
            publisher = src.get("publisher") or safe_api.get("publisher") or ""
            if location and publisher:
                seg += f". {location}: {publisher}"
            elif publisher:
                seg += f". {publisher}"
            if year:
                seg += f", {year}"
            parts.append(f"{seg}.")

    # ── Conference paper [C] ─────────────────────────────────────────────────
    elif ref_type in ("inproceedings", "conference", "proceedings"):
        # GB/T 7714 A.2: Title[C]//Conference Name. Location: Publisher, Year: Pages.
        conf_name = journal.strip()
        conference_location = src.get("conference_location") or safe_api.get("conference_location") or ""
        publisher = src.get("publisher") or safe_api.get("publisher") or ""

        # Build "Location: Publisher, Year: Pages" suffix
        conf_suffix = ". "
        if conference_location and publisher:
            conf_suffix += f"{conference_location}: {publisher}"
            if year:
                conf_suffix += f", {year}"
        elif conference_location:
            conf_suffix += conference_location
            if year:
                conf_suffix += f", {year}"
        elif publisher:
            conf_suffix += publisher
            if year:
                conf_suffix += f", {year}"
        elif year:
            conf_suffix += str(year)
        if pages:
            conf_suffix += f": {pages}"
        conf_suffix += "."

        # Concatenate title + conf_name to avoid space from " ".join(parts)
        if title:
            parts.append(f"{title}[C]//{conf_name}{conf_suffix}")
        else:
            parts.append(f"[C]//{conf_name}{conf_suffix}")

    # ── Unknown / other (defaults to journal article [J]) ──────────────────────
    else:
        # Format: Title[J]. Journal Name, Year, Volume(Issue):Pages.
        if title:
            parts.append(f"{title}[J].")
        if journal:
            seg = journal
            if year:
                seg += f", {year}"
            if volume:
                seg += f", {volume}"
                if issue:
                    seg += f"({issue})"
            if pages:
                seg += f": {pages}"
            parts.append(f"{seg}.")
        elif year:
            parts.append(f"{year}[J].")

    result = " ".join(parts)
    result = re.sub(r"  +", " ", result).strip()
    if result and not result.endswith("."):
        result += "."
    return result
