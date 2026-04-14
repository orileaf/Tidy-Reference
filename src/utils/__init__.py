# src/utils/__init__.py
"""Utility modules shared across all pipeline stages."""

from src.utils.constants import (
    BATCH_SIZE,
    RATE_LIMIT_DELAY,
    LLM_TIMEOUT,
    OUT_JSON,
    SYSTEM_PROMPT,
)
from src.utils.journals import JOURNAL_ABBR, JOURNAL_FULL
from src.utils.latex import clean_latex
from src.utils.bibtex import abbrev_journal, expand_journal, make_bibkey, bibtex_entry
from src.utils.merger import merge_results, extract_title_from_raw, PAPER_FIELDS
from src.utils.formatters import (
    format_authors_display,
    format_ieee,
    format_nature,
    _safe_api_data,
)

__all__ = [
    "BATCH_SIZE",
    "RATE_LIMIT_DELAY",
    "LLM_TIMEOUT",
    "OUT_JSON",
    "SYSTEM_PROMPT",
    "JOURNAL_ABBR",
    "JOURNAL_FULL",
    "clean_latex",
    "abbrev_journal",
    "expand_journal",
    "make_bibkey",
    "bibtex_entry",
    "format_authors_display",
    "format_ieee",
    "format_nature",
    "_safe_api_data",
    "merge_results",
    "extract_title_from_raw",
]
