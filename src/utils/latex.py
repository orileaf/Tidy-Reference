#!/usr/bin/env python3
"""
src/utils/latex.py — LaTeX special-character escaping.
"""


def clean_latex(s: str | None) -> str | None:
    """Escape special LaTeX characters."""
    if not s:
        return s
    s = str(s)
    for old, new in [
        ("&", r"\&"),
        ("%", r"\%"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
        ("—", "--"),
        ("–", "--"),
        ("\u201c", "``"),
        ("\u201d", "''"),
        ('"', "''"),
    ]:
        s = s.replace(old, new)
    return s
