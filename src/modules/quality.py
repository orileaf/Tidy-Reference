#!/usr/bin/env python3
"""
src/modules/quality.py — LLM quality assessment + interactive review workflow.

Input:  data/03_search/search_results.json  (from search module)
        data/01_raw/refs_raw.json           (raw citation text for context)

Output:
  python -m src.modules.quality              → runs QA judgment + interactive review
  python -m src.modules.quality --approve   → merge + bib_export_report.md

  data/04_quality/qa_results.json      — all entries with LLM QA judgment
  data/04_quality/qa_review.json       — medium+low entries only (dict, keyed by ref_id)
  data/04_quality/qa_approved.json     — [after --approve] merged high + approved medium/low
  data/05_export/bib_export_report.md — merged approval decisions + export warnings

Workflow:
  1. python -m src.modules.quality
     → LLM QA judgment (batches) → qa_results.json + qa_review.json
     → Interactive review → edit qa_review.json decisions
  2. python -m src.modules.quality --approve
     → Merges high + approved medium/low → qa_approved.json
     → Writes bib_export_report.md
"""

import json
import os
import re
import sys
import tty
import termios
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import get
from src.utils.llm_client import get_llm_response

# ── Config ─────────────────────────────────────────────────────────────────────

DATA          = Path(__file__).parent.parent.parent / "data"
STAGE_RAW     = DATA / "01_raw"
STAGE_SEARCH  = DATA / "03_search"
STAGE_QUAL    = DATA / "04_quality"
STAGE_EXP     = DATA / "05_export"

REFS_RAW        = STAGE_RAW    / "refs_raw.json"
SEARCH_JSON     = STAGE_SEARCH / "search_results.json"
QA_RESULTS_JSON = STAGE_QUAL   / "qa_results.json"
QA_REVIEW       = STAGE_QUAL   / "qa_review.json"
QA_REVIEW_BAK   = STAGE_QUAL   / "qa_review.json.bak"
QA_APPROVED     = STAGE_QUAL   / "qa_approved.json"
BIB_REPORT      = STAGE_EXP    / "bib_export_report.md"

# Legacy paths (read-only migration)
QA_MEDIUM_JSON = DATA / "qa_medium.json"
QA_LOW_JSON    = DATA / "qa_low.json"

QA_BATCH_SIZE      = 20
QA_MAX_CONCURRENCY = 5

VALID_PATCH_FIELDS = {"title", "authors", "journal", "year", "volume", "pages", "doi", "type"}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _save_json(data: dict, path: Path):
    """Atomic write: write to .tmp then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.rename(path)


def _read_raw_map() -> dict:
    """Load raw citation text keyed by ref_id int."""
    raw_map = {}
    if REFS_RAW.exists():
        with open(REFS_RAW, encoding="utf-8") as f:
            for r in json.load(f):
                raw_map[int(r["ref_id"])] = r
    return raw_map


# ── QA Prompt ─────────────────────────────────────────────────────────────────

QA_SYSTEM = """你是一个文献检索质检专家。你的职责是判断检索结果是否正确匹配了原始引用。

【第一步：核查是否有数据】
如果 crossref、semantic_scholar、mcp 三个渠道都返回"无数据"，Confidence 必须是 low。
没有任何证据，任何理由都不能给 medium 或 high。

【第二步：核查原始引用提供了多少信息】
阅读 raw_text 和 llm_parsed，判断原文中有哪些字段：
- 有 title：记为 has_title
- 有 author/author surname：记为 has_author
- 有 year：记为 has_year
- 有 journal：记为 has_journal
- 有 volume/pages：记为 has_page_info
- 有 DOI：记为 has_doi

【第三步：评估检索匹配质量】
将检索到的 title 与原文的 title（或 raw_text 中的标题部分）做比较。
- 标题高度相似（≥0.6）：title_match = high
- 标题部分相似（0.3–0.6）：title_match = partial
- 标题完全不同（<0.3）或无标题：title_match = low

其他字段对比（authors、year、journal）：
- 完全一致或格式等价（如"Tang X" vs "Xi Tang"）：一致
- 有线索但不确定（如姓氏对得上）：基本一致
- 明显矛盾：冲突

【评分标准】
high（同时满足）：
  1. 有至少2个渠道返回数据，且主要字段一致；或只有1个渠道但 title_match=high
  2. 原文信息量足够（有 title + 至少一个其他关键字段：author/year/journal）
  3. 标题匹配良好（title_match=high）
  4. 无关键冲突（无标题完全不同、无年份差>2年、无完全不同的期刊）

medium（满足1，或满足2且无重大冲突）：
  1. 有数据，title_match=high，但原文信息量不足（仅有title，无其他字段）
  2. 有数据，title_match=partial，主要字段基本一致
  注意：页码差异（如结果815-820 vs 原文818）不算冲突，title_match 依然为 high

low（满足任一即 low）：
  1. 三个渠道均无数据（no_retrieval）
  2. 标题完全不匹配（title_match=low）
  3. 有关键冲突：标题匹配但期刊完全不同；或年份差>3年
  4. 原文标题存在，检索结果也返回了数据，但两者标题完全不一致
  5. 多个渠道之间数据相互矛盾

你必须输出一个严格的JSON数组，每个元素对应一条引用。
"""


def _build_qa_prompt(batch: list, raw_map: dict) -> str:
    entries_text = []
    for item in batch:
        rid = item["ref_id"]
        raw_text = raw_map.get(rid, {}).get("raw_text", "")[:400]

        def _fmt(d):
            if not d:
                return "无数据"
            parts = []
            for k in ["title", "authors", "journal", "year", "volume", "issue", "pages", "doi"]:
                v = d.get(k)
                if v:
                    parts.append(f"{k}={v}")
            return ", ".join(parts) if parts else "无数据"

        def _raw_fields(d):
            if not d:
                return "无"
            present = [k for k in ["title", "authors", "journal", "year", "volume", "pages", "doi"]
                       if d.get(k)]
            return ", ".join(present) if present else "无"

        entries_text.append(
            f'{{"ref_id": {rid}, "strategy": "{item.get("strategy_used", "")}", '
            f'"raw_text": "{_escape_json(raw_text)}", '
            f'"llm_parsed": {{{_fmt(item.get("llm_data", {}))}}}, '
            f'"llm_parsed_fields": "[{_raw_fields(item.get("llm_data", {}))}]", '
            f'"crossref": {{{_fmt(item.get("crossref"))}}}, '
            f'"semantic_scholar": {{{_fmt(item.get("semantic_scholar"))}}}, '
            f'"mcp": {{{_fmt(item.get("mcp"))}}}}}'
        )

    return (
        "以下是待质检的引用列表（JSON数组）：\n\n"
        "[\n  " + ",\n  ".join(entries_text) + "\n]\n\n"
        "对每条引用，严格按照系统提示中的【评分标准】判断其检索结果是否正确。\n"
        "注意：首先判断各渠道是否返回了\"无数据\"（若是，直接给 low）。\n"
        "输出格式（严格JSON数组，不要其他内容）：\n"
        '[{"ref_id": N, "confidence": "high|medium|low", '
        '"reason": "判断理由（必须说明：原文有哪些字段、检索到哪些、对比结论）", '
        '"agreed_fields": ["title", "authors"], '
        '"disagreed_fields": []}, ...]'
    )


def _escape_json(s: str) -> str:
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "")
             .replace("\t", "\\t"))


# ── QA batch dispatch ──────────────────────────────────────────────────────────

def _call_qa_llm(batch: list, raw_map: dict) -> list[dict]:
    prompt = _build_qa_prompt(batch, raw_map)
    try:
        result = get_llm_response(batch, system=QA_SYSTEM, user=prompt)
        if isinstance(result, dict):
            result = [result]
        validated = []
        for item in result:
            if isinstance(item, dict) and item.get("ref_id") is not None:
                if item.get("confidence") not in ("high", "medium", "low"):
                    item["confidence"] = "low"
                item.setdefault("reason", "无理由（API异常）")
                item.setdefault("agreed_fields", [])
                item.setdefault("disagreed_fields", [])
                validated.append(item)
            else:
                validated.append({"ref_id": item.get("ref_id", 0), "confidence": "low",
                                  "reason": "LLM返回格式错误", "agreed_fields": [], "disagreed_fields": []})
        return validated
    except json.JSONDecodeError as e:
        print(f"  QA JSON error batch {[r['ref_id'] for r in batch]}: {e}")
        return [{"ref_id": r["ref_id"], "confidence": "low",
                 "reason": f"JSON解析失败: {e}", "agreed_fields": [], "disagreed_fields": []}
                for r in batch]
    except Exception as e:
        print(f"  QA error batch {[r['ref_id'] for r in batch]}: {e}")
        return [{"ref_id": r["ref_id"], "confidence": "low",
                 "reason": f"调用失败: {e}", "agreed_fields": [], "disagreed_fields": []}
                for r in batch]


# ── Build qa_review.json ──────────────────────────────────────────────────────

def _build_review_data(search_results: list) -> dict:
    """Build the qa_review.json dict: only medium+low entries, keyed by ref_id str."""
    review_entries = {}
    for e in search_results:
        if e["qa"]["confidence"] == "high":
            continue
        rid = str(int(e["ref_id"]))
        # Strip private fields from the source entry
        entry_copy = {k: v for k, v in e.items() if not k.startswith("_")}
        review_entries[rid] = {
            "_approved": None,
            "_reviewed_at": None,
            "_review_note": None,
            "_patch": None,
            "_decision": "pending",
            **entry_copy,
        }

    medium_count = sum(1 for e in search_results if e["qa"]["confidence"] == "medium")
    low_count    = sum(1 for e in search_results if e["qa"]["confidence"] == "low")

    return {
        "_meta": {
            "generated_at": datetime.now().isoformat(),
            "total": len(review_entries),
            "medium_count": medium_count,
            "low_count": low_count,
            "reviewed_count": 0,
            "approved_count": 0,
            "skipped_count": 0,
        },
        "entries": review_entries,
    }


# ── Legacy migration ──────────────────────────────────────────────────────────

def _migrate_legacy_review_files() -> dict | None:
    """One-way migration from legacy qa_medium.json / qa_low.json → qa_review.json dict format."""
    if QA_REVIEW.exists():
        return None
    if not (QA_MEDIUM_JSON.exists() or QA_LOW_JSON.exists()):
        return None

    review_entries = {}
    for label, path in [("medium", QA_MEDIUM_JSON), ("low", QA_LOW_JSON)]:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for e in json.load(f):
                rid = str(int(e["ref_id"]))
                approved = e.pop("_approved", None)
                decision = ("approved" if approved is True
                            else "skipped" if approved is False
                            else "pending")
                review_entries[rid] = {
                    "_approved": approved,
                    "_reviewed_at": None,
                    "_review_note": f"(migrated from legacy {label} file; decision: {decision})",
                    "_patch": None,
                    "_decision": decision,
                    **{k: v for k, v in e.items() if not k.startswith("_")},
                }

    total = len(review_entries)
    medium_count = sum(
        1 for e in review_entries.values()
        if e.get("qa", {}).get("confidence") == "medium"
    )
    return {
        "_meta": {
            "generated_at": datetime.now().isoformat(),
            "total": total,
            "medium_count": medium_count,
            "low_count": total - medium_count,
            "reviewed_count": sum(1 for e in review_entries.values() if e["_decision"] != "pending"),
            "approved_count": sum(1 for e in review_entries.values() if e["_decision"] == "approved"),
            "skipped_count": sum(1 for e in review_entries.values() if e["_decision"] == "skipped"),
            "_migrated": True,
        },
        "entries": review_entries,
    }


# ── Export warnings (reused from export.py logic) ─────────────────────────────

def _check_warnings(entry: dict, final_data: dict) -> list[str]:
    """Return a list of warning strings for one entry."""
    warnings = []
    strategy = entry.get("strategy_used", "")

    if not final_data.get("type"):
        warnings.append("missing_type")
    if not final_data.get("title"):
        warnings.append("missing_title")
    if not final_data.get("journal"):
        warnings.append("missing_journal")
    if not final_data.get("year"):
        warnings.append("missing_year")
    if not final_data.get("volume"):
        warnings.append("missing_volume")
    if not final_data.get("pages"):
        warnings.append("missing_pages")
    if not final_data.get("doi"):
        warnings.append("missing_doi")

    llm_type = (entry.get("llm_data") or {}).get("type", "").lower()
    cr_type = (entry.get("crossref") or {}).get("type", "").lower()
    if llm_type == "article" and cr_type in ("book-chapter", "book", "proceedings"):
        warnings.append(f"type_mismatch:llm={llm_type} cr={cr_type}")
    elif llm_type in ("book", "book-chapter") and cr_type == "article":
        warnings.append(f"type_mismatch:llm={llm_type} cr={cr_type}")

    if strategy == "mcp_fallback":
        mcp_data = entry.get("mcp") or {}
        mcp_url = mcp_data.get("source_url")
        mcp_doi = mcp_data.get("doi")
        if not mcp_url and not mcp_doi:
            warnings.append("mcp_no_url_or_doi")
        mcp_fields = [mcp_data.get("journal"), mcp_data.get("year"),
                      mcp_data.get("volume"), mcp_data.get("pages"), mcp_doi]
        missing_count = sum(1 for v in mcp_fields if not v)
        if missing_count >= 3:
            warnings.append(f"mcp_incomplete:{missing_count}/5 fields missing")
        if mcp_url:
            warnings.append(f"mcp_url:{mcp_url[:80]}")

    return warnings


# ── bib_export_report.md generation ──────────────────────────────────────────

def _generate_export_report(
    all_results: list,
    review_entries: dict,
    approved: list,
) -> list[dict]:
    """Generate bib_export_report.md and return warnings_list for approved entries."""
    STAGE_EXP.mkdir(parents=True, exist_ok=True)

    high_entries  = [r for r in all_results if r["qa"].get("confidence") == "high"]
    medium_review = [e for e in review_entries.values() if e["qa"]["confidence"] == "medium"]
    low_review    = [e for e in review_entries.values() if e["qa"]["confidence"] == "low"]

    approved_ids = {int(e["ref_id"]) for e in approved}

    # Strategy breakdown
    strat_counts: dict[str, int] = {}
    for r in all_results:
        s = r.get("strategy_used", "unknown")
        strat_counts[s] = strat_counts.get(s, 0) + 1

    total = len(all_results)
    high_count    = len(high_entries)
    med_approved  = sum(1 for e in medium_review if e.get("_approved") is True)
    low_approved  = sum(1 for e in low_review    if e.get("_approved") is True)
    reviewed      = sum(1 for e in review_entries.values() if e["_decision"] != "pending")
    exported      = len(approved)

    # Build warnings for approved entries
    warnings_list = []
    for entry in approved:
        # Rebuild final_data from entry (simplified — just use agreed fields)
        patch = entry.get("_patch") or {}
        sources = {
            "mcp": entry.get("mcp"),
            "crossref": entry.get("crossref"),
            "semantic_scholar": entry.get("semantic_scholar"),
            "llm": entry.get("llm_data"),
        }
        PAPER_FIELDS = ["authors", "title", "journal", "year", "volume", "issue", "pages", "doi"]
        final_data = {}
        for f in PAPER_FIELDS:
            for tag, src in [("mcp", sources.get("mcp")),
                             ("crossref", sources.get("crossref")),
                             ("ss", sources.get("semantic_scholar")),
                             ("llm", sources.get("llm"))]:
                v = (src or {}).get(f) if isinstance(src, dict) else None
                if v is not None and str(v).strip():
                    final_data[f] = str(v).strip()
                    break
            if f in patch and patch[f]:
                final_data[f] = str(patch[f])
        final_data["type"] = patch.get("type") or (sources.get("llm") or {}).get("type")
        warns = _check_warnings(entry, final_data)
        if warns:
            warnings_list.append({
                "ref_id": entry["ref_id"],
                "title": (final_data.get("title") or "")[:60],
                "warnings": warns,
            })

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(BIB_REPORT, "w", encoding="utf-8") as f:
        f.write("# Bibliography Export Report\n\n")
        f.write(f"Generated: {now}\n\n")

        # Summary
        f.write("## Summary\n\n")
        f.write("| Metric | Count |\n")
        f.write("|--------|------:|\n")
        f.write(f"| Total references processed | {total} |\n")
        f.write(f"| Auto-approved (high confidence) | {high_count} |\n")
        f.write(f"| Manually reviewed | {reviewed} |\n")
        f.write(f"| Approved (medium+low) | {med_approved + low_approved} |\n")
        f.write(f"| Rejected/Skipped (medium+low) | {(len(medium_review) + len(low_review)) - (med_approved + low_approved)} |\n")
        f.write(f"| **Final entries exported** | **{exported}** |\n")
        f.write("\n")

        # Search strategy breakdown
        f.write("## Search Strategy Breakdown\n\n")
        f.write("| Strategy | Count | % |\n")
        f.write("|---------|------:|--:|\n")
        for s, c in sorted(strat_counts.items()):
            pct = round(c / total * 100) if total else 0
            f.write(f"| {s} | {c} | {pct}% |\n")
        f.write("\n")

        # Entries requiring review
        f.write("## Entries Requiring Review\n\n")

        def _decision_badge(decision: str) -> str:
            if decision in ("approved", "patched"):
                return "✅ Approved"
            elif decision == "skipped":
                return "⏸️ Skipped"
            else:
                return "⏳ Pending"

        def _trunc(s: str, n: int = 60) -> str:
            return (s[:n] + "…") if len(s) > n else s

        def _short_reason(reason: str) -> str:
            # Strip Chinese annotation for compact display
            return reason[:120]

        def _channels(e: dict) -> str:
            ch = []
            if e.get("crossref"):           ch.append("CR")
            if e.get("semantic_scholar"):   ch.append("S2")
            if e.get("mcp"):                ch.append("MCP")
            return "+".join(ch) if ch else "—"

        # Medium section
        f.write(f"### ⚠️ Medium Confidence ({len(medium_review)} entries)\n\n")
        if medium_review:
            f.write("| ref_id | strategy | channels | reason (truncated) | agreed | disagreed | decision |\n")
            f.write("|--------|---------|---------|-------------------|--------|----------|----------|\n")
            for e in sorted(medium_review, key=lambda x: int(x["ref_id"])):
                qa = e.get("qa", {})
                agreed   = ", ".join(qa.get("agreed_fields", [])) or "—"
                disagreed = ", ".join(qa.get("disagreed_fields", [])) or "—"
                f.write(
                    f"| {int(e['ref_id'])} | {e.get('strategy_used','')} "
                    f"| {_channels(e)} "
                    f"| {_trunc(_short_reason(qa.get('reason','')), 40)} "
                    f"| {agreed} | {disagreed} "
                    f"| {_decision_badge(e.get('_decision','pending'))} |\n"
                )
        else:
            f.write("None.\n")
        f.write("\n")

        # Low section
        f.write(f"### ❌ Low Confidence ({len(low_review)} entries)\n\n")
        if low_review:
            f.write("| ref_id | strategy | channels | reason (truncated) | decision |\n")
            f.write("|--------|---------|---------|-------------------|----------|\n")
            for e in sorted(low_review, key=lambda x: int(x["ref_id"])):
                qa = e.get("qa", {})
                f.write(
                    f"| {int(e['ref_id'])} | {e.get('strategy_used','')} "
                    f"| {_channels(e)} "
                    f"| {_trunc(_short_reason(qa.get('reason','')), 50)} "
                    f"| {_decision_badge(e.get('_decision','pending'))} |\n"
                )
        else:
            f.write("None.\n")
        f.write("\n")

        # Patches applied
        patched = [e for e in review_entries.values() if e.get("_patch")]
        if patched:
            f.write("### 🔧 Patches Applied\n\n")
            f.write("| ref_id | field | original value | patched value |\n")
            f.write("|--------|-------|----------------|---------------|\n")
            for e in patched:
                patch = e.get("_patch", {})
                for fld, val in patch.items():
                    f.write(f"| {int(e['ref_id'])} | {fld} | (see qa_review.json) | {val} |\n")
            f.write("\n")

        # Export warnings
        if warnings_list:
            f.write("## Export Warnings\n\n")
            f.write(f"> **{len(warnings_list)} entries with issues — review before publication*\n\n")

            # Group by warning type
            by_type: dict[str, list] = {}
            for w in warnings_list:
                for warn in w["warnings"]:
                    key = warn.split(":")[0]
                    by_type.setdefault(key, []).append(w)

            for wtype, entries in sorted(by_type.items(), key=lambda x: (
                ["missing_type", "mcp_incomplete", "mcp_no_url_or_doi",
                 "missing_doi", "missing_title", "missing_journal",
                 "missing_year", "missing_volume", "missing_pages",
                 "type_mismatch", "mcp_url", "ambiguous_author"].index(x[0])
                if x[0] in ["missing_type", "mcp_incomplete", "mcp_no_url_or_doi",
                             "missing_doi", "missing_title", "missing_journal",
                             "missing_year", "missing_volume", "missing_pages",
                             "type_mismatch", "mcp_url", "ambiguous_author"]
                else 99
            )):
                f.write(f"### {wtype} ({len(entries)} entries)\n\n")
                f.write("| ref_id | title | warnings |\n")
                f.write("|--------|-------|----------|\n")
                for w in sorted(entries, key=lambda x: int(x["ref_id"])):
                    f.write(f"| {w['ref_id']} | {w['title'][:50]} | {', '.join(w['warnings'])} |\n")
                f.write("\n")
        else:
            f.write("## Export Warnings\n\n")
            f.write("No warnings.\n")

    print(f"  bib_export_report.md → {BIB_REPORT}")
    return warnings_list


# ── run_qa ────────────────────────────────────────────────────────────────────

def run_qa():
    """Run LLM QA judgment; produce qa_results.json + qa_review.json."""
    for _d in [STAGE_RAW, STAGE_SEARCH, STAGE_QUAL, STAGE_EXP]:
        _d.mkdir(parents=True, exist_ok=True)

    if not SEARCH_JSON.exists():
        print(f"ERROR: {SEARCH_JSON} not found — run 'python -m src.modules.search' first.")
        sys.exit(1)

    with open(SEARCH_JSON, encoding="utf-8") as f:
        search_results = json.load(f)

    raw_map = _read_raw_map()
    search_map = {int(r["ref_id"]): r for r in search_results}

    print(f"Loaded {len(search_results)} search results")
    print(f"Running LLM QA in batches of {QA_BATCH_SIZE}...")

    batches = [search_results[i:i + QA_BATCH_SIZE]
               for i in range(0, len(search_results), QA_BATCH_SIZE)]

    all_qa = []
    done = 0

    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(batches), desc="  QA batches", unit="batch", ncols=80)
        _pbar_write = pbar.write
    except ImportError:
        pbar = None
        _pbar_write = print

    with ThreadPoolExecutor(max_workers=QA_MAX_CONCURRENCY) as executor:
        futures = {executor.submit(_call_qa_llm, b, raw_map): b for b in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                qa_batch = future.result()
            except Exception as exc:
                _pbar_write(f"  QA batch error {[r['ref_id'] for r in batch]}: {exc}")
                qa_batch = [{"ref_id": r["ref_id"], "confidence": "low",
                             "reason": f"执行异常: {exc}", "agreed_fields": [], "disagreed_fields": []}
                            for r in batch]
            all_qa.extend(qa_batch)
            done += 1
            if pbar:
                pbar.update(1)
                high = sum(1 for q in qa_batch if q["confidence"] == "high")
                ids = [b["ref_id"] for b in batch]
                pbar.set_postfix_str(f"batch {ids[0]}–{ids[-1]} high={high}/{len(qa_batch)}")
            else:
                ids = [r["ref_id"] for r in batch]
                high = sum(1 for q in qa_batch if q["confidence"] == "high")
                _pbar_write(f"  batch {ids[0]}–{ids[-1]}  high={high}/{len(qa_batch)}  done={done}/{len(batches)}")

    if pbar:
        pbar.close()

    # Merge QA into search results
    qa_map = {int(q["ref_id"]): q for q in all_qa if q.get("ref_id") is not None}
    for r in search_results:
        rid = int(r["ref_id"])
        r["qa"] = qa_map.get(rid, {"ref_id": rid, "confidence": "low",
                                    "reason": "无QA结果", "agreed_fields": [], "disagreed_fields": []})

    # Save qa_results.json
    _save_json(search_results, QA_RESULTS_JSON)

    # Build and save qa_review.json
    review_data = _build_review_data(search_results)
    if QA_REVIEW.exists():
        # Check if existing file has real decisions — if so, preserve them
        try:
            existing = json.loads(QA_REVIEW.read_text(encoding="utf-8"))
            existing_entries = existing.get("entries", {})
            pending = [rid for rid, e in existing_entries.items() if e.get("_decision") == "pending"]
            if not pending and sum(1 for e in existing_entries.values() if e.get("_decision") != "pending") > 0:
                # All entries already reviewed — skip overwriting
                print(f"  qa_review.json already complete — skipping (all {len(existing_entries)} entries reviewed)")
                _save_json(json.loads(QA_RESULTS_JSON.read_text(encoding="utf-8")), QA_RESULTS_JSON)
                print(f"\n══ QA already done ══")
                print(f"  Run 'python -m src.modules.quality --review' to re-enter review.")
                return
            elif pending:
                print(f"  qa_review.json has {len(pending)} pending — re-run QA (backup saved)")
        except Exception:
            pass
        _save_json(json.loads(QA_REVIEW.read_text(encoding="utf-8")), QA_REVIEW_BAK)
        print(f"  Backed up previous qa_review.json → qa_review.json.bak")
    _save_json(review_data, QA_REVIEW)

    high_n   = sum(1 for r in search_results if r["qa"]["confidence"] == "high")
    medium_n = sum(1 for r in search_results if r["qa"]["confidence"] == "medium")
    low_n    = sum(1 for r in search_results if r["qa"]["confidence"] == "low")

    print(f"\n══ QA done ══")
    print(f"  high={high_n}  medium={medium_n}  low={low_n}")
    print(f"  qa_results.json  → {QA_RESULTS_JSON}")
    print(f"  qa_review.json  → {QA_REVIEW}")
    print(f"  (run interactive review next, then --approve)")


# ── run_approve ───────────────────────────────────────────────────────────────

def run_approve():
    """Merge approved entries and generate bib_export_report.md."""
    for _d in [STAGE_QUAL, STAGE_EXP]:
        _d.mkdir(parents=True, exist_ok=True)

    # Migration from legacy files
    migration = _migrate_legacy_review_files()
    if migration:
        _save_json(migration, QA_REVIEW)
        print("Migrated legacy qa_medium.json / qa_low.json → qa_review.json")

    if not QA_REVIEW.exists():
        print(f"ERROR: {QA_REVIEW} not found — run 'python -m src.modules.quality' first.")
        sys.exit(1)

    with open(QA_REVIEW, encoding="utf-8") as f:
        review_data = json.load(f)

    if not QA_RESULTS_JSON.exists():
        print(f"ERROR: {QA_RESULTS_JSON} not found — run 'python -m src.modules.quality' first.")
        sys.exit(1)
    with open(QA_RESULTS_JSON, encoding="utf-8") as f:
        all_results = json.load(f)

    result_map = {int(r["ref_id"]): r for r in all_results}
    review_entries = review_data["entries"]

    # Build approved list
    approved = []
    for rid_str, entry in review_entries.items():
        if entry.get("_approved") is not True:
            continue
        rid = int(rid_str)
        base = dict(result_map.get(rid, {}))
        base["qa"] = entry.get("qa", base.get("qa", {}))
        base["_approved_via"] = entry["qa"].get("confidence", "medium")
        patch = entry.get("_patch")
        if patch:
            base["_patch"] = patch
        base["_review_note"] = entry.get("_review_note", "")
        base["_decision"] = entry.get("_decision", "approved")
        approved.append(base)

    # High-confidence auto-include
    high_entries = []
    for r in all_results:
        if r["qa"].get("confidence") == "high":
            base = dict(r)
            base["_approved_via"] = "high"
            base["_decision"] = "auto"
            approved.append(base)
            high_entries.append(base)

    approved.sort(key=lambda x: int(x["ref_id"]))
    _save_json(approved, QA_APPROVED)

    # Generate merged report
    _generate_export_report(all_results, review_entries, approved)

    high_count   = len(high_entries)
    med_approved  = sum(1 for e in review_entries.values()
                        if e.get("_approved") is True and e["qa"].get("confidence") == "medium")
    low_approved  = sum(1 for e in review_entries.values()
                        if e.get("_approved") is True and e["qa"].get("confidence") == "low")
    med_total     = sum(1 for e in review_entries.values() if e["qa"].get("confidence") == "medium")
    low_total     = sum(1 for e in review_entries.values() if e["qa"].get("confidence") == "low")

    print(f"Approved: {len(approved)} entries "
          f"(high={high_count}, "
          f"medium approved={med_approved}/{med_total}, "
          f"low approved={low_approved}/{low_total})")
    print(f"  qa_approved.json  → {QA_APPROVED}")


# ── Interactive review ────────────────────────────────────────────────────────

def _getch() -> str:
    """Single-character input without Enter (Unix)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def _decide(review_data: dict, rid: str, approved: bool) -> dict:
    """Apply approve/skip decision to one entry and save immediately."""
    entry = review_data["entries"][rid]
    now = datetime.now().isoformat()
    entry["_approved"] = approved
    entry["_reviewed_at"] = now
    entry["_decision"] = "approved" if approved else "skipped"
    meta = review_data["_meta"]
    meta["reviewed_count"] += 1
    if approved:
        meta["approved_count"] += 1
    else:
        meta["skipped_count"] += 1
    _save_json(review_data, QA_REVIEW)
    return entry


def _do_patch(review_data: dict, rid: str) -> None:
    """Prompt for field patches, apply, save."""
    print("  Patch format: field=value  (comma-separated for multiple, empty to cancel)")
    print("  Available: title, authors, journal, year, volume, pages, doi, type")
    print("  Example: authors=Smith J, year=2023")
    print("  > ", end="", flush=True)
    line = sys.stdin.readline().strip()
    if not line:
        return
    entry = review_data["entries"][rid]
    patch = {}
    for part in line.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if k in VALID_PATCH_FIELDS and v:
            patch[k] = v
    if patch:
        now = datetime.now().isoformat()
        entry["_patch"] = patch
        entry["_approved"] = True
        entry["_reviewed_at"] = now
        entry["_decision"] = "patched"
        meta = review_data["_meta"]
        meta["reviewed_count"] += 1
        meta["approved_count"] += 1
        _save_json(review_data, QA_REVIEW)
        print(f"  ✅ Patched {patch} — approved automatically.")


# ── ANSI Colors ────────────────────────────────────────────────────────────────
import os as _os
_FORCE_COLOR = _os.environ.get("FORCE_COLOR")
if _FORCE_COLOR or _os.isatty(sys.stdout.fileno()):
    _CC = {"r": "\033[91m", "g": "\033[92m", "y": "\033[93m",
            "b": "\033[94m", "c": "\033[96m", "d": "\033[2m",
            "x": "\033[0m",  "B": "\033[1m"}
else:
    _CC = {k: "" for k in list("rgbycdxB")}

def _c(s, col):
    return f"{_CC[col]}{s}{_CC['x']}" if _CC[col] else s

def _t(s, n=38):
    return (s[:n] + "…") if len(s) > n else (s or "")

def _print_field_table(llm, cr, s2, mcp, agreed, disagreed):
    FIELDS = [("title","Title"),("authors","Authors"),
              ("journal","Journal"),("year","Year"),
              ("volume","Vol"),("pages","Pages"),("doi","DOI")]
    sep = "  " + _c("─"*74, "d")
    print(sep)
    print(f"  {_c('Field','d'):<8}  {_c('LLM','b'):<38}  "
          f"{_c('CR','g'):<38}  {_c('S2','y'):<38}  {_c('MCP','c'):<38}")
    print(sep)
    any_row = False
    for fk, fl in FIELDS:
        lv = (llm.get(fk) or ""); cv = (cr.get(fk) or "")
        sv = (s2.get(fk) or "");   mv = (mcp.get(fk) or "")
        if not (lv or cv or sv or mv):
            continue
        any_row = True
        col = "r" if fk in disagreed else ("g" if fk in agreed else "")
        def _cell(v, cc):
            if not v: return _c("—", "d")
            return _c(_t(v), cc)
        lbl = _c(f"{fl:<8}", col or "d")
        print(f"  {lbl}  {_cell(lv,'b'):<38}  {_cell(cv,'g'):<38}  "
              f"{_cell(sv,'y'):<38}  {_cell(mv,'c'):<38}")
    if not any_row:
        print(f"  {_c('  (no structured fields)', 'd')}")


def _display_card(rid, entry, idx, total, raw_map, meta):
    """Print one entry card for review."""
    qa        = entry.get("qa", {})
    conf      = qa.get("confidence", "?")
    strategy  = entry.get("strategy_used", "")
    badge_txt = "⚠ medium" if conf == "medium" else "✗ low"
    badge_col = "y"          if conf == "medium" else "r"

    cr   = entry.get("crossref")          or {}
    s2   = entry.get("semantic_scholar") or {}
    mcp  = entry.get("mcp")              or {}
    llm  = entry.get("llm_data")         or {}

    def _ch(d): return "●" if d else "○"
    channels = f"CR{_ch(cr)}  S2{_ch(s2)}  MCP{_ch(mcp)}"

    agreed    = qa.get("agreed_fields", [])
    disagreed = qa.get("disagreed_fields", [])
    rid_int   = int(rid)
    raw_text  = (raw_map.get(rid_int, {}).get("raw_text", "") or "")[:110]
    reason    = qa.get("reason", "")[:140]
    patch     = entry.get("_patch")
    decision  = entry.get("_decision", "pending")

    sep = _c("─" * 72, "d")
    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  {_c(f'ref #{rid_int}', 'B')}  {_c(badge_txt, badge_col)}  "
          f"|  {strategy}  |  {channels}")
    # Progress bar
    done  = meta["reviewed_count"]
    skip  = meta["skipped_count"]
    bw    = 44
    bar   = _c("█" * int(done / total * bw if total else 0), "g") + \
            _c("░" * max(0, bw - int(done / total * bw if total else 0)), "d")
    print(f"  [{bar}]  {_c('✅' + str(done), 'g')}  "
          f"{_c('⏸' + str(skip), 'd')}  "
          f"{_c('⏳' + str(total - done), 'y')}")

    # ── Raw citation ───────────────────────────────────────────────────────────
    print(f"  {_c('RAW', 'd')}: {raw_text}")

    # ── Field table ────────────────────────────────────────────────────────────
    _print_field_table(llm, cr, s2, mcp, agreed, disagreed)

    # ── LLM reason ─────────────────────────────────────────────────────────────
    if reason:
        print(f"\n  {_c('LLM: ', 'd')}{_t(reason, 120)}")

    # ── Disagreements ─────────────────────────────────────────────────────────
    if disagreed:
        print(f"  {_c('⚔ disagree: ', 'r')}{', '.join(disagreed)}")

    # ── Patch ──────────────────────────────────────────────────────────────────
    if patch:
        items = "  ".join(f"{k}={v}" for k, v in patch.items())
        print(f"  {_c('🔧 patch: ', 'y')}{items}")

    print(sep)
    # ── Commands ────────────────────────────────────────────────────────────────
    print(f"  [{_c('A', 'g')}]pprove  [{_c('S', 'r')}kip  "
          f"[{_c('E', 'y')}dit/patch  [{_c('P', 'b')}##-##  "
          f"[{_c('D', 'd')}one/skip-all  [{_c('Q', 'd')}uit")
    print(f"  {_c('> ', 'd')}", end="", flush=True)


def run_review():
    """Interactive review loop for qa_review.json entries."""
    if not QA_REVIEW.exists():
        print(f"ERROR: {QA_REVIEW} not found.")
        sys.exit(1)

    with open(QA_REVIEW, encoding="utf-8") as f:
        review_data = json.load(f)

    raw_map = _read_raw_map()
    entries = review_data["entries"]
    meta = review_data["_meta"]

    pending = sorted(
        [rid for rid, e in entries.items() if e["_decision"] == "pending"],
        key=int
    )
    already_reviewed = sum(1 for e in entries.values() if e["_decision"] != "pending")

    print(f"\n══ Interactive Review: {len(pending)} entries to review "
          f"({already_reviewed} already reviewed) ══")
    print(f"  ⚠️ medium={meta['medium_count']}  ❌ low={meta['low_count']}")
    print(f"  Saving decisions to: {QA_REVIEW}")
    print()

    idx = 0
    while idx < len(pending):
        rid = pending[idx]
        entry = entries[rid]
        _display_card(rid, entry, idx, len(pending), raw_map, meta)
        print("> ", end="", flush=True)

        raw = _getch()
        key = raw.lower()

        if key in ("\r", "\n", ""):
            # Enter / empty → approve current
            _decide(review_data, rid, True)
        elif key == "a":
            _decide(review_data, rid, True)
        elif key == "s":
            _decide(review_data, rid, False)
        elif key == "e":
            _do_patch(review_data, rid)
            # Re-fetch after save (meta counts updated)
            with open(QA_REVIEW, encoding="utf-8") as f:
                review_data = json.load(f)
            entries = review_data["entries"]
            # Re-sort pending (current was just reviewed)
            pending = sorted(
                [r for r, e in entries.items() if e["_decision"] == "pending"],
                key=int
            )
            continue
        elif key == "p":
            # Range approve — consume the \n after getch, then read the rest
            # (getch only reads 1 char, so \n is still in stdin)
            try:
                import fcntl, os
                fd = sys.stdin.fileno()
                fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
                # drain any extra chars
                try:
                    while True:
                        sys.stdin.read(1)
                except Exception:
                    pass
                fcntl.fcntl(fd, fcntl.F_SETFL, fl)
            except Exception:
                pass
            line = sys.stdin.readline().strip()
            ids = _parse_approve_range(line, pending)
            for target_rid in ids:
                if target_rid in entries and entries[target_rid]["_decision"] == "pending":
                    _decide(review_data, target_rid, True)
                    with open(QA_REVIEW, encoding="utf-8") as f:
                        review_data = json.load(f)
                    entries = review_data["entries"]
            pending = sorted(
                [r for r, e in entries.items() if e["_decision"] == "pending"],
                key=int
            )
            continue
        elif key == "d":
            # Skip all remaining
            for r in pending[idx:]:
                if entries[r]["_decision"] == "pending":
                    _decide(review_data, r, False)
            break
        elif key == "q":
            _save_json(review_data, QA_REVIEW)
            print("  Saved progress — run 'python -m src.modules.quality --approve' to generate report when ready.")
            sys.exit(0)
        else:
            # Invalid key — re-prompt same entry
            idx -= 1

        idx += 1

    # End of review loop
    with open(QA_REVIEW, encoding="utf-8") as f:
        final_data = json.load(f)
    final_meta = final_data["_meta"]
    print()
    print("══ Review complete ══")
    print(f"  Reviewed:  {final_meta['reviewed_count']}")
    print(f"  Approved:  {final_meta['approved_count']}")
    print(f"  Skipped:   {final_meta['skipped_count']}")

    if final_meta["reviewed_count"] == final_meta["total"]:
        print()
        print("  All entries reviewed — generating report now...")
        print()
        run_approve()
    else:
        print(f"\nRun 'python -m src.modules.quality --approve' to generate qa_approved.json + bib_export_report.md")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    for _d in [STAGE_RAW, STAGE_SEARCH, STAGE_QUAL, STAGE_EXP]:
        _d.mkdir(parents=True, exist_ok=True)

    if "--approve" in sys.argv:
        run_approve()
    elif "--review" in sys.argv:
        run_review()
    else:
        run_qa()


if __name__ == "__main__":
    main()
