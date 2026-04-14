#!/usr/bin/env python3
"""
src/utils/journals.py — Journal name abbreviation and expansion maps,
plus shared normalization for matching.
"""

import re

JOURNAL_ABBR = {
    "Physical Review Letters": "Phys. Rev. Lett.",
    "Physical Review A": "Phys. Rev. A",
    "Physical Review B": "Phys. Rev. B",
    "Physical Review E": "Phys. Rev. E",
    "Physical Review X": "Phys. Rev. X",
    "Physics Letters A": "Phys. Lett. A",
    "Physics Letters B": "Phys. Lett. B",
    "Physics Reports": "Phys. Rep.",
    "Science": "Science",
    "Nature Photonics": "Nat. Photonics",
    "Nature": "Nature",
    "Nature Communications": "Nat. Commun.",
    "Scientific Reports": "Sci. Rep.",
    "Science Advances": "Sci. Adv.",
    "Applied Physics Letters": "Appl. Phys. Lett.",
    "Applied Physics B": "Appl. Phys. B",
    "Applied Optics": "Appl. Opt.",
    "Optics Express": "Opt. Express",
    "Optics Letters": "Opt. Lett.",
    "Optics Communications": "Opt. Commun.",
    "Optical and Quantum Electronics": "Opt. Quantum Electron.",
    "Optical Review": "Opt. Rev.",
    "IEEE Journal of Quantum Electronics": "IEEE J. Quantum Electron.",
    "IEEE Journal of Selected Topics in Quantum Electronics": "IEEE J. Sel. Topics Quantum Electron.",
    "IEEE Journal of Lightwave Technology": "IEEE J. Lightw. Technol.",
    "IEEE Photonics Technology Letters": "IEEE Photonics Technol. Lett.",
    "IEEE Photonics Journal": "IEEE Photonics J.",
    "IEEE Transactions on Microwave Theory and Techniques": "IEEE Trans. Microw. Theory Technol.",
    "IEEE Transactions on Antennas and Propagation": "IEEE Trans. Antennas Propag.",
    "IEEE Access": "IEEE Access",
    "Light: Science & Applications": "Light Sci. Appl.",
    "Chinese Optics Letters": "Chin. Opt. Lett.",
    "Chaos, Solitons & Fractals": "Chaos Solitons Fract.",
    "Chaos": "Chaos",
    "Chaos: An Interdisciplinary Journal of Nonlinear Science": "Chaos: Interdiscip. J. Nonlinear Sci.",
    "Laser & Photonics Reviews": "Laser Photonics Rev.",
    "Review of Scientific Instruments": "Rev. Sci. Instrum.",
    "Journal of the Atmospheric Sciences": "J. Atmos. Sci.",
    "Journal of Infrared and Millimeter Waves": "J. Infrared Millim. Waves",
    "International Journal of Bifurcation and Chaos": "Int. J. Bifurcat. Chaos",
    "Mathematical Problems in Engineering": "Math. Probl. Eng.",
    "American Mathematical Monthly": "Am. Math. Mon.",
    "Results in Physics": "Results Phys.",
    "ACS Photonics": "ACS Photonics",
    "Optical Engineering": "Opt. Eng.",
    "Photonics Research": "Photon. Res.",
    "APL Photonics": "APL Photonics",
    "The European Physical Journal Special Topics": "Eur. Phys. J. Spec. Top.",
    "Acta Physica Sinica": "Acta Phys. Sin.",
    "Acta Photonica Sinica": "Acta Photonica Sinica",
    "Journal of the European Optical Society": "J. Eur. Opt. Soc.",
    "Journal of Modern Optics": "J. Mod. Opt.",
    "Journal of Optics": "J. Opt.",
    "Journal of the Optical Society of America B": "J. Opt. Soc. Am. B",
    "Semiconductor Lasers: Stability, Instability and Chaos": "Semiconductor Lasers",
    "Journal of the Optical Society of America": "J. Opt. Soc. Am.",
    "Journal of Lightwave Technology": "J. Lightw. Technol.",
    "Advanced Quantum Technologies": "Adv. Quantum Technol.",
    "Advanced Photonics": "Adv. Photonics",
    "Proceedings of SPIE": "Proc. SPIE",
    "Quantum Science and Technology": "Quantum Sci. Technol.",
    "Frontiers in Optics": "Front. Optoelectron.",
    "Nanophotonics": "Nanophotonics",
    "Photonics and Nanostructures - Fundamentals and Applications": "Photon. Nanostructures",
    "IEE Proceedings - Optoelectronics": "IEE Proc. Optoelectron.",
    "Journal of Display Technology": "J. Display Technol.",
    "Electronics Letters": "Electron. Lett.",
    "Applied Physics Express": "Appl. Phys. Express",
    "Japanese Journal of Applied Physics": "Jpn. J. Appl. Phys.",
    "Journal of the Physical Society of Japan": "J. Phys. Soc. Jpn.",
    "Physical Review Applied": "Phys. Rev. Appl.",
    "Physical Review Photonics": "Phys. Rev. Photonics",
    "Optoelectronics, IEE Proceedings": "IEE Proc. Optoelectron.",
    "IEEE Journal of Quantum Electronics": "IEEE J. Quantum Electron.",
    "Journal of the European Optical Society Part B": "J. Eur. Opt. Soc. Part B",
}

JOURNAL_FULL = {v: k for k, v in JOURNAL_ABBR.items()}

# ── Normalization helpers ───────────────────────────────────────────────────────

_ABBR_EXPANSION = {
    "phys rev lett": "physical review letters",
    "phys rev a": "physical review a",
    "phys rev b": "physical review b",
    "phys rev e": "physical review e",
    "phys rev x": "physical review x",
    "phys rev appl": "physical review applied",
    "phys lett a": "physics letters a",
    "phys lett b": "physics letters b",
    "phys rep": "physics reports",
    "nat photonics": "nature photonics",
    "nat commun": "nature communications",
    "sci rep": "scientific reports",
    "sci adv": "science advances",
    "appl phys lett": "applied physics letters",
    "appl phys b": "applied physics b",
    "appl opt": "applied optics",
    "opt express": "optics express",
    "opt lett": "optics letters",
    "opt commun": "optics communications",
    "opt quantum electron": "optical and quantum electronics",
    "opt rev": "optical review",
    "ieee j quantum electron": "ieee journal of quantum electronics",
    "ieee j sel topics quantum electron": "ieee journal of selected topics in quantum electronics",
    "ieee j lightw technol": "ieee journal of lightwave technology",
    "ieee photonics technol lett": "ieee photonics technology letters",
    "ieee photonics j": "ieee photonics journal",
    "j lightw technol": "journal of lightwave technology",
    "ieee trans microw theory technol": "ieee transactions on microwave theory and techniques",
    "ieee trans antennas propag": "ieee transactions on antennas and propagation",
    "ieee access": "ieee access",
    "light sci appl": "light science and applications",
    "chin opt lett": "chinese optics letters",
    "chaos solitons fract": "chaos solitons and fractals",
    "chaos interdiscip j nonlinear sci": "chaos interdisciplinary journal of nonlinear science",
    "laser photonics rev": "laser and photonics reviews",
    "rev sci instrum": "review of scientific instruments",
    "j atmos sci": "journal of the atmospheric sciences",
    "j infrared millim waves": "journal of infrared and millimeter waves",
    "int j bifurc chaos": "international journal of bifurcation and chaos",
    "int": "international",
    "math probl eng": "mathematical problems in engineering",
    "am math mon": "american mathematical monthly",
    "results phys": "results in physics",
    "acs photonics": "acs photonics",
    "opt eng": "optical engineering",
    "photon res": "photonics research",
    "apl photonics": "apl photonics",
    "eur phys j spec top": "the european physical journal special topics",
    "acta phys sin": "acta physica sinica",
    "j eur opt soc": "journal of the european optical society",
    "j mod opt": "journal of modern optics",
    "j opt soc am b": "journal of the optical society of america b",
    "j opt soc am": "journal of the optical society of america",
    "j lightw Technol": "journal of lightwave technology",
    "adv quantum technol": "advanced quantum technologies",
    "adv photonics": "advanced photonics",
    "proc spie": "proceedings of spie",
    "quantum sci technol": "quantum science and technology",
    "front optoelectron": "frontiers in optoelectronics",
    "nanophotonics": "nanophotonics",
    "photon nanostructures": "photonics and nanostructures fundamentals and applications",
    "ieee proc optoelectron": "ieee proceedings optoelectronics",
    "j display technol": "journal of display technology",
    "electron lett": "electronics letters",
    "appl phys express": "applied physics express",
    "jpn j appl phys": "japanese journal of applied physics",
    "j phys soc jpn": "journal of the physical society of japan",
    "opt soc am b": "optical society of america b",
    "opt soc am": "optical society of america",
    "j quantum electron": "journal of quantum electronics",
    "inst phys conf ser": "institute of physics conference series",
    "europhys lett": "europhysics letters",
    "j phys a": "journal of physics a",
    "j phys b": "journal of physics b",
    "new j phys": "new journal of physics",
}


def normalize_journal(j: str) -> str:
    """
    Normalize a journal name to its canonical full form for comparison.
    Handles both abbreviated (Phys. Rev. Lett.) and full (Physical Review Letters) input.
    Returns a lowercase string with punctuation removed.
    """
    if not j:
        return ""
    original = j.strip()
    # Direct lookup: abbreviation → full
    if original in JOURNAL_FULL:
        return JOURNAL_FULL[original].lower()
    # Direct lookup: full → full (already canonical)
    if original in JOURNAL_ABBR:
        return original.lower()
    # Try stripping common trailing qualifiers like ", pp. xxx"
    base = re.sub(r",\s*(pp?|vol|tome|fasc|ser|part).*$", "", original, flags=re.I).strip()
    if base in JOURNAL_FULL:
        return JOURNAL_FULL[base].lower()
    if base in JOURNAL_ABBR:
        return base.lower()
    # Strip punctuation and lowercase as last resort
    return re.sub(r"[^a-z0-9]", "", original.lower())


def _word_parts(text: str) -> list[str]:
    """Split journal name into individual word-parts (handling abbreviations)."""
    # Split on spaces and all common separator punctuation
    parts = re.split(r"[\s.:;,\-–—/&]+", text)
    return [p for p in parts if p]  # drop empty strings


def _expand_abbr(s: str) -> str:
    """
    Normalize a journal string for comparison:
    1. Split into word-parts (handles "Phys. Rev. Lett." → ["phys","rev","lett"])
    2. Expand known multi-word abbreviation sequences
    3. Join back into space-separated string
    """
    words = _word_parts(s.lower())   # normalize to lowercase upfront
    if not words:
        return ""

    # Expand known abbreviation sequences (longer patterns first)
    sorted_patterns = sorted(_ABBR_EXPANSION.keys(), key=len, reverse=True)
    expanded = []
    i = 0
    while i < len(words):
        matched = False
        for pat in sorted_patterns:
            pat_words = pat.split()
            pat_len = len(pat_words)
            if tuple(words[i:i + pat_len]) == tuple(pat_words):
                expanded.append(_ABBR_EXPANSION[pat])
                i += pat_len
                matched = True
                break
        if not matched:
            expanded.append(words[i].lower())
            i += 1

    return " ".join(expanded)


def _normalize_for_compare(s: str) -> list[str]:
    """
    Normalize and split into ordered word list (no concat — preserves word boundaries).
    - Remove 'and'/'&'
    - Keep only alphabetic chars
    - Drop 1-letter tokens (abbreviation single letters get merged with next word)
    - Deduplicate adjacent identical words
    """
    # Remove 'and'/'&'
    t = re.sub(r"\band\b", " ", s, flags=re.I)
    t = re.sub(r"&", " ", t)
    # Keep only alphabetic
    words = re.findall(r"[a-z]+", t.lower())
    # Drop 1-letter tokens
    words = [w for w in words if len(w) >= 2]
    # Deduplicate adjacent
    deduped = []
    for w in words:
        if not deduped or deduped[-1] != w:
            deduped.append(w)
    return deduped


def journals_match(qj: str, rj: str) -> bool:
    """
    Return True if query journal (qj) and result journal (rj) refer to the same journal.
    Uses ordered word-list comparison to handle abbreviated vs full-name forms.
    """
    if not qj or not rj:
        return True

    qj_exp = _expand_abbr(qj)
    rj_exp = _expand_abbr(rj)

    if qj_exp == rj_exp:
        return True

    w_qj = _normalize_for_compare(qj_exp)
    w_rj = _normalize_for_compare(rj_exp)

    if not w_qj or not w_rj:
        return True

    # Exact word-list match
    if w_qj == w_rj:
        return True

    # One word list is a prefix/superset of the other (common case: same words, minor diffs)
    shorter, longer = (w_qj, w_rj) if len(w_qj) <= len(w_rj) else (w_rj, w_qj)
    shorter_set = set(shorter)
    longer_set = set(longer)

    # Exact word match
    if shorter_set == longer_set:
        return True

    # High containment: ≥80% of shorter's words are in longer
    if shorter_set and len(shorter_set & longer_set) / len(shorter_set) >= 0.8:
        return True

    # Word-based Jaccard with min denominator
    inter = len(shorter_set & longer_set)
    union = min(len(shorter_set), len(longer_set))
    jaccard = inter / union if union else 0

    if jaccard > 0.5:
        return True

    # Fallback: high character-overlap between individual tokens
    # Handles cases like "bifurc" vs "bifurcation", "technol" vs "technology"
    # Count pairs where shorter token is a prefix/suffix/substring of a longer token
    soft_matches = sum(
        1 for sw in shorter_set
        if any(
            (sw in lw or lw[:5] == sw[:5])  # prefix match or common root (5+ chars)
            for lw in longer_set
        )
    )
    return soft_matches / len(shorter_set) >= 0.75 if shorter_set else False

