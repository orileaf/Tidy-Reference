#!/usr/bin/env python3
"""
src/modules/parser.py — Parse .docx/.txt files into raw reference entries.

Input:  a .docx or .txt file path (positional argument).
        Defaults to project_root / "1.docx".
Output: data/01_raw/refs_raw.json  —  [{"ref_id": int, "raw_text": str}, ...]

CLI:
  python -m src.modules.parser                    # uses project_root/1.docx
  python -m src.modules.parser path/to/file.docx
  python -m src.modules.parser path/to/file.txt
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import python_docx_path  # noqa: F401 — may be needed if python-docx is not in default site-packages


# ── Reference header detection ─────────────────────────────────────────────────

REF_HEADER_PATTERNS = [
    (r"^\[(\d+)\](?:\.\s*|\s+)(.)", "[N] or [N]. header"),
    (r"^(\d+)\.\s+(.)", "N. header"),
    (r"^(\d+)\s+([A-Z])", "bare N  header"),
]


def is_ref_header(text: str):
    """Return ref_id (int) if text starts a reference header, else None."""
    text = text.strip()
    for pattern, _label in REF_HEADER_PATTERNS:
        m = re.match(pattern, text)
        if m:
            return int(m.group(1))
    return None


def strip_ref_header(text: str):
    """Remove reference number prefix from the first line of a reference."""
    text = re.sub(r"^\[\d+\](?:\.\s*|\s+)", "", text)
    text = re.sub(r"^\d+\.\s+", "", text)
    text = re.sub(r"^\d+\s+", "", text)
    return text


# ── Document parsing ────────────────────────────────────────────────────────────

def extract_from_docx(docx_path: str) -> list[dict]:
    """Extract all reference paragraphs from a .docx file."""
    from docx import Document

    doc = Document(docx_path)
    raw_paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return _extract_from_paragraphs(raw_paras)


def extract_from_txt(txt_path: str) -> list[dict]:
    """Extract all reference entries from a plain-text file.

    Handles two modes:
      1. Blank-line separated entries (each block is one reference)
      2. Continuous paragraphs when blank-line separation is unreliable
         (falls back to reference header detection)
    """
    with open(txt_path, encoding="utf-8") as f:
        content = f.read()

    # Try blank-line separation first
    blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
    if len(blocks) >= 10:
        # Looks like blank-line separated; treat each block as a ref
        return _extract_from_paragraphs(blocks)

    # Fall back to paragraph-level processing
    paras = [p.strip() for p in content.split("\n") if p.strip()]
    return _extract_from_paragraphs(paras)


def _extract_from_paragraphs(raw_paras: list[str]) -> list[dict]:
    """Parse a flat list of paragraph strings into reference entries."""
    refs = []
    i = 0

    while i < len(raw_paras):
        para = raw_paras[i]
        ref_id = is_ref_header(para)
        if ref_id is None:
            i += 1
            continue

        ref_text = strip_ref_header(para)

        # Accumulate continuation paragraphs until the next header
        j = i + 1
        while j < len(raw_paras):
            nxt = raw_paras[j]
            if is_ref_header(nxt) is not None:
                break
            ref_text += " " + nxt
            j += 1

        i = j
        refs.append({"ref_id": ref_id, "raw_text": ref_text.strip()})

    refs.sort(key=lambda x: x["ref_id"])
    return refs


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    project_root = Path(__file__).parent.parent.parent

    # Allow positional argument for input file
    if sys.argv[1:]:
        input_path = Path(sys.argv[1])
        if not input_path.is_absolute():
            input_path = project_root / input_path
    else:
        input_path = project_root / "1.docx"

    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    suffix = input_path.suffix.lower()
    if suffix == ".docx":
        refs = extract_from_docx(str(input_path))
    elif suffix == ".txt":
        refs = extract_from_txt(str(input_path))
    else:
        print(f"ERROR: unsupported file type '{suffix}' — use .docx or .txt", file=sys.stderr)
        sys.exit(1)

    out_path = project_root / "data" / "01_raw" / "refs_raw.json"
    (out_path.parent).mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(refs, f, ensure_ascii=False, indent=2)

    # Warn about missing ref_ids if the count is suspiciously close to 154
    all_ids = {r["ref_id"] for r in refs}
    if len(refs) >= 100:
        expected = set(range(1, max(all_ids) + 1))
        missing = sorted(expected - all_ids)
        if missing:
            print(f"  WARNING — missing ref_ids: {missing[:20]}{' ...' if len(missing) > 20 else ''}")
        else:
            print(f"  All {len(refs)} ref_ids found (1–{max(all_ids)}).")

    print(f"Extracted {len(refs)} references → {out_path}")


if __name__ == "__main__":
    main()
