#!/usr/bin/env python3
"""
src/modules/llm_parse.py — LLM structured output from raw references.

Input:  data/refs_raw.json   [{"ref_id": int, "raw_text": str}, ...]
Output: data/llm_results.json  (stream-save, crash-safe)

Strategy:
  1. Partition into batches of LLM_BATCH_SIZE=2.
  2. Dispatch batches in parallel with ThreadPoolExecutor(max_workers=5).
  3. After each batch → stream-save to data/.llm_results_tmp.json.
  4. Retry failed batches sequentially (single-ref).
  5. Validate LLM output fields before accepting; retry on format error.

CLI:
  python -m src.modules.llm_parse
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import get
from src.utils.llm_client import get_llm_response

# ── Config ─────────────────────────────────────────────────────────────────────

LLM_BATCH_SIZE = 2        # refs per LLM API call
LLM_MAX_CONCURRENCY = 5   # parallel workers

IN_JSON  = "data/01_raw/refs_raw.json"
OUT_JSON = "data/02_llm/llm_results.json"
TMP_OUT  = "data/02_llm/.llm_results_tmp.json"

# ── Output schema ──────────────────────────────────────────────────────────────

PAPER_FIELDS = ["authors", "title", "journal", "year", "volume", "issue", "pages", "doi"]
EXTRA_FIELDS = ["type"]


def _skeleton(ref_id) -> dict:
    return {
        "ref_id": ref_id,
        "status": "not_found",
        "authors": None, "title": None, "journal": None,
        "year": None, "volume": None, "issue": None,
        "pages": None, "doi": None,
        "type": None,
        "field_confidence": {},
    }


def _validate_entry(entry: dict) -> bool:
    """Check that LLM returned a well-formed entry with required keys."""
    if not isinstance(entry, dict):
        return False
    if entry.get("ref_id") is None:
        return False
    # DOI must match pattern if present
    doi = entry.get("doi")
    if doi and not re.match(r"^10\.\d+/", str(doi)):
        return False
    # Year must be 4 digits if present
    year = entry.get("year")
    if year and not re.match(r"^\d{4}$", str(year)):
        return False
    return True


def _normalize_confidence(entry: dict) -> dict:
    """Convert LLM confidence labels (extracted/known/null) to merger-style labels.

    In llm_results.json: "extracted" | "known" | "null"
    After merge:         "raw" | "crossref" | "semantic_scholar" | "null"
    We store "raw" for extracted/known, and "null" for null.
    """
    fc = entry.get("field_confidence") or {}
    normalized = {}
    for k in PAPER_FIELDS + EXTRA_FIELDS:
        v = fc.get(k)
        if v in ("extracted", "known"):
            normalized[k] = "raw"
        else:
            normalized[k] = "null"
    entry["field_confidence"] = normalized
    return entry


# ── Batch dispatch ─────────────────────────────────────────────────────────────

def _call_llm_batch(batch: list) -> tuple[list, set]:
    """Call LLM for one batch. Returns (parsed_list, failed_ids_set)."""
    ids = [int(r["ref_id"]) for r in batch]
    failed_ids: set[int] = set()
    try:
        result = get_llm_response(batch)
        if isinstance(result, dict):
            result = [result]
        # Validate each entry
        parsed = []
        for entry in result:
            if _validate_entry(entry):
                parsed.append(_normalize_confidence(entry))
            else:
                failed_ids.add(int(entry.get("ref_id", 0)))
                parsed.append(_skeleton(entry.get("ref_id")))
        return parsed, failed_ids
    except json.JSONDecodeError as e:
        print(f"\n  JSON decode error in batch {ids}: {e}")
        failed_ids.update(ids)
        return [_skeleton(r["ref_id"]) for r in batch], failed_ids
    except Exception as e:
        print(f"\n  ERROR batch {ids[0]}–{ids[-1]}: {e}")
        failed_ids.update(ids)
        return [_skeleton(r["ref_id"]) for r in batch], failed_ids


def _retry_one(ref: dict) -> dict:
    """Retry a single reference individually."""
    try:
        result = get_llm_response([ref])
        if isinstance(result, list):
            result = result[0]
        if _validate_entry(result):
            return _normalize_confidence(result)
        return _skeleton(ref["ref_id"])
    except Exception as e:
        print(f"  retry [{ref['ref_id']}] failed: {e}")
        return _skeleton(ref["ref_id"])


# ── Stream save ────────────────────────────────────────────────────────────────

def _stream_save(results: list, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    project_root = Path(__file__).parent.parent.parent
    refs_path = project_root / IN_JSON
    out_path = project_root / OUT_JSON
    tmp_path = project_root / TMP_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not refs_path.exists():
        print(f"ERROR: {refs_path} not found — run 'python -m src.modules.parser' first.")
        sys.exit(1)

    with open(refs_path, encoding="utf-8") as f:
        refs = json.load(f)

    total = len(refs)
    model = get("OPENAI_MODEL", "MiniMax-M2")
    base_url = get("OPENAI_BASE_URL", "")
    provider_label = f"{model} @ {base_url}" if base_url else model
    print(f"Loaded {total} references from {refs_path}")
    print(f"LLM: {provider_label}  (batch={LLM_BATCH_SIZE}, concurrency={LLM_MAX_CONCURRENCY})")
    print()

    batches = [refs[i:i + LLM_BATCH_SIZE] for i in range(0, total, LLM_BATCH_SIZE)]
    print(f"  {len(batches)} batches of up to {LLM_BATCH_SIZE} refs each")

    # ── Parallel LLM dispatch ──────────────────────────────────────────────────
    print("══ Step 1: LLM parsing ══")
    llm_results = []
    all_failed_ids = set()

    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(batches), desc="  LLM batches", unit="batch", ncols=80)
    except ImportError:
        pbar = None

    with ThreadPoolExecutor(max_workers=LLM_MAX_CONCURRENCY) as executor:
        futures = {executor.submit(_call_llm_batch, b): b for b in batches}
        for future in as_completed(futures):
            batch = futures[future]
            parsed, failed_ids = future.result()
            llm_results.extend(parsed)
            all_failed_ids.update(failed_ids)
            if pbar:
                pbar.update(1)
                ok = sum(1 for p in parsed if p.get("status") != "not_found")
                ids = [r["ref_id"] for r in batch]
                pbar.set_postfix_str(f"batch {ids[0]}–{ids[-1]} {ok}/{len(batch)} OK")

    if pbar:
        pbar.close()

    # Stream-save after all parallel batches
    _stream_save(llm_results, tmp_path)
    ok_count = total - len(all_failed_ids)
    print(f"  Parallel done: {ok_count}/{total} OK  ({len(all_failed_ids)} failed)")

    # ── Retry failed single-ref ───────────────────────────────────────────────
    if all_failed_ids:
        print(f"\n══ Step 2: Retry {len(all_failed_ids)} failed refs ══")
        retry_refs = [r for r in refs if int(r["ref_id"]) in {int(x) for x in all_failed_ids}]
        for ref in retry_refs:
            result = _retry_one(ref)
            for i, item in enumerate(llm_results):
                if int(item["ref_id"]) == int(ref["ref_id"]):
                    llm_results[i] = result
                    break
            else:
                llm_results.append(result)
            _stream_save(llm_results, tmp_path)
            status = result.get("status", "?")
            print(f"  retry [{ref['ref_id']}]  status={status}")

    # ── Final save ─────────────────────────────────────────────────────────────
    llm_results.sort(key=lambda x: int(x["ref_id"]))
    _stream_save(llm_results, out_path)
    if tmp_path.exists():
        tmp_path.unlink()

    final_ok = sum(1 for r in llm_results if r.get("status") != "not_found")
    print(f"\n══ llm_results.json → {out_path} ({final_ok}/{total} OK) ══")


if __name__ == "__main__":
    main()
