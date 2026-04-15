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
import shutil
import sys
import tty
import termios
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import get
from src.utils.llm_client import get_llm_response

# ── Terminal sizing ─────────────────────────────────────────────────────────────

TERMINAL_WIDTH = shutil.get_terminal_size().columns if sys.stdout.isatty() else 120
LABEL_W = 8   # width reserved for field label column
CELL_GAP = 4  # spaces between columns
NUM_COLS = 4  # LLM, CR, S2, MCP
COL_W = max(20, (TERMINAL_WIDTH - 2 - LABEL_W - CELL_GAP * (NUM_COLS + 1)) // NUM_COLS)


def _wrap(text: str, width: int) -> list[str]:
    """Wrap text into lines of at most `width` chars at word boundaries."""
    if not text:
        return [""]
    lines = []
    for paragraph in text.split("\n"):
        while paragraph:
            if len(paragraph) <= width:
                lines.append(paragraph)
                break
            # Find last space within width
            cut = paragraph[:width]
            last_space = cut.rfind(" ")
            if last_space > width // 2:
                lines.append(cut[:last_space])
                paragraph = paragraph[last_space + 1:]
            else:
                lines.append(cut)
                paragraph = paragraph[width:]
    return lines if lines else [""]


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
MANUAL_RESEARCH_JSON = STAGE_QUAL / "manual_research.json"
MANUAL_REVIEW_JSON  = STAGE_QUAL / "manual_review.json"

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
            "_approved": False,
            "_reviewed_at": None,
            "_review_note": None,
            "_patch": None,
            "_decision": "pending",
            "_source": "auto_search",
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
                    "_source": "auto_search",
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

    review_entries = review_data["entries"]

    # Build approved list from two sources: qa_results.json (base) + qa_review.json (overrides).
    # qa_review.json has higher priority for any overlapping ref_ids.
    approved_rids: set[int] = set()
    approved = []

    # Start from qa_results.json as the base
    for r in all_results:
        rid = int(r["ref_id"])
        reviewed_entry = review_entries.get(str(rid), {})
        # Skip if not approved in qa_review.json and not high-confidence in qa_results.json
        if reviewed_entry.get("_approved") is not True and r["qa"].get("confidence") != "high":
            continue
        base = dict(r)
        # Override with qa_review.json entry if it exists
        if reviewed_entry:
            base.update({
                k: v for k, v in reviewed_entry.items()
                if k not in ("crossref", "semantic_scholar", "mcp", "llm_data", "strategy_used")
            })
            # qa from qa_review.json overrides qa_results.json (contains latest manual QA)
            if "qa" in reviewed_entry:
                base["qa"] = reviewed_entry["qa"]
        # Determine _approved_via label
        if reviewed_entry and reviewed_entry.get("manual_data") is not None:
            base["_source"] = "manual_search"
            conf = base.get("qa", {}).get("confidence", "medium")
            base["_approved_via"] = "manual_high" if conf == "high" else f"manual_{conf}"
        elif reviewed_entry and reviewed_entry.get("_approved") is True:
            base["_source"] = "manual_review"
            base["_approved_via"] = base.get("qa", {}).get("confidence", "medium")
        else:
            base["_approved_via"] = "high"
            base["_decision"] = "auto"
            base["_source"] = "auto_search"
        approved_rids.add(rid)
        approved.append(base)

    approved.sort(key=lambda x: int(x["ref_id"]))
    _save_json(approved, QA_APPROVED)

    # Generate merged report
    _generate_export_report(all_results, review_entries, approved)

    high_count   = sum(1 for e in approved if e.get("_approved_via") == "high")
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
    """Truncate at word boundary, add ellipsis."""
    if not s:
        return ""
    if len(s) <= n:
        return s
    cut = s[:n]
    last_space = cut.rfind(" ")
    if last_space > n // 2:
        return cut[:last_space] + "…"
    return cut + "…"


def _print_field_table(llm, cr, s2, mcp, agreed, disagreed):
    FIELDS = [("title","Title"),("authors","Authors"),
              ("journal","Journal"),("year","Year"),
              ("volume","Vol"),("pages","Pages"),("doi","DOI")]
    sep = "  " + _c("─" * (LABEL_W + CELL_GAP + COL_W * NUM_COLS + CELL_GAP * 2), "d")
    print(sep)
    headers = "  " + f"{_c('Field', 'd'):<{LABEL_W}}" + " " * CELL_GAP + \
              "  ".join(f"{_c(src.upper(), color):<{COL_W}}"
                        for src, color in [("LLM","b"),("CR","g"),("S2","y"),("MCP","c")])
    print(headers)
    print(sep)

    any_row = False
    for fk, fl in FIELDS:
        lv = llm.get(fk) or ""; cv = cr.get(fk) or ""
        sv = s2.get(fk) or "";   mv = mcp.get(fk) or ""
        if not (lv or cv or sv or mv):
            continue
        any_row = True

        l_lines = _wrap(lv, COL_W)
        c_lines = _wrap(cv, COL_W)
        s_lines = _wrap(sv, COL_W)
        m_lines = _wrap(mv, COL_W)
        max_lines = max(len(l_lines), len(c_lines), len(s_lines), len(m_lines))

        col = "r" if fk in disagreed else ("g" if fk in agreed else "")

        for i in range(max_lines):
            lc = l_lines[i] if i < len(l_lines) else ""
            cc = c_lines[i] if i < len(c_lines) else ""
            sc = s_lines[i] if i < len(s_lines) else ""
            mc = m_lines[i] if i < len(m_lines) else ""

            def _cell(v, cc):
                return _c(v, cc) if v else _c("—", "d")
            row_lbl = (f"{_c(fl + ':', col or 'd'):<{LABEL_W}}") if i == 0 else " " * LABEL_W
            print(f"  {row_lbl}  {_cell(lc, col or 'b'):<{COL_W}}  "
                  f"{_cell(cc, col or 'g'):<{COL_W}}  "
                  f"{_cell(sc, col or 'y'):<{COL_W}}  "
                  f"{_cell(mc, col or 'c'):<{COL_W}}")

    if not any_row:
        print(f"  {_c('  (no structured fields)', 'd')}")
    print(sep)


def _display_card(rid, entry, idx, total, raw_map, meta):
    """Print one entry card for review."""
    # If manual_data is present, show raw vs manual comparison instead of 4-channel table
    if entry.get("manual_data"):
        _print_manual_card(rid, entry, raw_map, meta)
        return

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
    raw_text  = raw_map.get(rid_int, {}).get("raw_text", "") or ""
    reason    = qa.get("reason", "") or ""
    patch     = entry.get("_patch")
    decision  = entry.get("_decision", "pending")

    CARD_W = TERMINAL_WIDTH - 4
    sep = _c("─" * CARD_W, "d")
    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  {_c(f'ref #{rid_int}', 'B')}  {_c(badge_txt, badge_col)}  "
          f"|  {strategy}  |  {channels}")
    # Progress bar
    done  = meta["reviewed_count"]
    skip  = meta["skipped_count"]
    bw    = max(20, CARD_W - 20)
    bar   = _c("█" * int(done / total * bw if total else 0), "g") + \
            _c("░" * max(0, bw - int(done / total * bw if total else 0)), "d")
    print(f"  [{bar}]  {_c('✅' + str(done), 'g')}  "
          f"{_c('⏸' + str(skip), 'd')}  "
          f"{_c('⏳' + str(total - done), 'y')}")

    # ── Raw citation (word-wrapped) ───────────────────────────────────────────
    RAW_W = TERMINAL_WIDTH - 10
    raw_lines = _wrap(raw_text, RAW_W)
    for i, line in enumerate(raw_lines):
        prefix = f"  {_c('RAW', 'd')}: " if i == 0 else "  " + " " * 7 + " "
        print(prefix + line)

    # ── Field table ────────────────────────────────────────────────────────────
    _print_field_table(llm, cr, s2, mcp, agreed, disagreed)

    # ── LLM reason (word-wrapped) ─────────────────────────────────────────────
    if reason:
        reason_lines = _wrap(reason, RAW_W)
        for i, line in enumerate(reason_lines):
            prefix = f"  {_c('LLM: ', 'd')}" if i == 0 else "  " + " " * 7 + " "
            print(prefix + line)

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


def _print_manual_card(rid, entry, raw_map, meta):
    """Print a card comparing raw citation vs manual_data (no CR/S2/MCP channels)."""
    rid_int = int(rid)
    raw_text = raw_map.get(rid_int, {}).get("raw_text", "") or ""
    manual = entry.get("manual_data") or {}
    qa = entry.get("qa", {})
    conf = qa.get("confidence", "?")
    badge_txt = "✅ high" if conf == "high" else f"⚠ {conf}"
    badge_col = "g" if conf == "high" else "y"

    CARD_W = TERMINAL_WIDTH - 4
    sep = _c("─" * CARD_W, "d")

    def _fmt(d: dict) -> str:
        if not d:
            return "—"
        parts = [f"{k}={d.get(k,'')}" for k in ["title", "authors", "journal", "year", "volume", "pages", "doi"]
                 if d.get(k)]
        return ", ".join(parts) or "—"

    done  = meta["reviewed_count"]
    skip  = meta["skipped_count"]
    bw    = max(20, CARD_W - 20)
    bar   = _c("█" * int(done / meta.get("total", 1) * bw if meta.get("total") else 0), "g") + \
            _c("░" * max(0, bw - int(done / meta.get("total", 1) * bw if meta.get("total") else 0)), "d")

    RAW_W = TERMINAL_WIDTH - 10

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  {_c(f'ref #{rid_int}', 'B')}  {_c(badge_txt, badge_col)}  |  manual_data")
    print(f"  [{bar}]  {_c('✅' + str(done), 'g')}  "
          f"{_c('⏸' + str(skip), 'd')}  "
          f"{_c('⏳' + str(meta.get('total', 1) - done), 'y')}")
    print(sep)

    # ── RAW ──────────────────────────────────────────────────────────────────
    raw_lines = _wrap(raw_text, RAW_W)
    for i, line in enumerate(raw_lines):
        prefix = f"  {_c('RAW', 'd')}: " if i == 0 else "  " + " " * 7 + " "
        print(prefix + line)

    # ── Manual data fields ────────────────────────────────────────────────────
    manual_lines = _wrap(_fmt(manual), RAW_W)
    for i, line in enumerate(manual_lines):
        prefix = f"  {_c('MANUAL', 'g')}: " if i == 0 else "  " + " " * 8 + " "
        print(prefix + line)

    # ── LLM reason ─────────────────────────────────────────────────────────────
    reason = qa.get("reason", "") or ""
    if reason:
        reason_lines = _wrap(reason, RAW_W)
        for i, line in enumerate(reason_lines):
            prefix = f"  {_c('LLM: ', 'd')}" if i == 0 else "  " + " " * 7 + " "
            print(prefix + line)

    # ── Disagreements ─────────────────────────────────────────────────────────
    disagreed = qa.get("disagreed_fields", [])
    if disagreed:
        print(f"  {_c('⚔ disagree: ', 'r')}{', '.join(disagreed)}")

    # ── Patch ──────────────────────────────────────────────────────────────────
    patch = entry.get("_patch")
    if patch:
        items = "  ".join(f"{k}={v}" for k, v in patch.items())
        print(f"  {_c('🔧 patch: ', 'y')}{items}")

    print(sep)
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
        [rid for rid, e in entries.items() if e["_approved"] is False],
        key=int
    )
    already_reviewed = sum(1 for e in entries.values() if e["_approved"] is not False)

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
                [r for r, e in entries.items() if e["_approved"] is False],
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
                if target_rid in entries and entries[target_rid]["_approved"] is False:
                    _decide(review_data, target_rid, True)
                    with open(QA_REVIEW, encoding="utf-8") as f:
                        review_data = json.load(f)
                    entries = review_data["entries"]
            pending = sorted(
                [r for r, e in entries.items() if e["_approved"] is False],
                key=int
            )
            continue
        elif key == "d":
            # Skip all remaining
            for r in pending[idx:]:
                if entries[r]["_approved"] is False:
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

    # Always call run_approve() after review — it rebuilds qa_approved.json from current qa_review.json
    # _approved=True entries (approved by user or auto-approved by --manual) are included,
    # _approved=False entries are excluded. Safe to call even if review was partial or full.
    print()
    print("  Generating qa_approved.json from current qa_review.json...")
    print()
    run_approve()
    _reset_manual_research_json()


# ── Manual Research Workflow ───────────────────────────────────────────────────

def _load_raw_map() -> dict:
    """Load raw citation text keyed by ref_id int."""
    raw_map = {}
    if REFS_RAW.exists():
        with open(REFS_RAW, encoding="utf-8") as f:
            for e in json.load(f):
                raw_map[int(e["ref_id"])] = e
    return raw_map


def _reset_manual_research_json():
    """将 qa_review.json 中 _approved == False 的条目写入 manual_research.json，
    research_text 强制置为 'null'，parsed 置为 None。"""
    if not QA_REVIEW.exists():
        return
    with open(QA_REVIEW, encoding="utf-8") as f:
        review = json.load(f)

    entries_out = {}
    for rid_str, entry in review.get("entries", {}).items():
        if entry.get("_approved") is False:
            rid = int(rid_str)
            raw_text = _read_raw_map().get(rid, {}).get("raw_text", "")
            entries_out[rid_str] = {
                "ref_id": rid,
                "research_text": "null",
                "parsed": None,
            }

    data = {
        "_meta": {
            "updated_at": datetime.now().isoformat(),
            "total": len(entries_out),
            "source": "reset_from_qa_review",
        },
        "entries": entries_out,
    }
    _save_json(data, MANUAL_RESEARCH_JSON)
    count = len(entries_out)
    print(f"  manual_research.json 重置 → {MANUAL_RESEARCH_JSON}（{count} 条待手动搜索）")


def _init_manual_research_json():
    """
    Initialize manual_research.json with all ref_ids that are pending or skipped
    in qa_review.json. If the file already exists, preserve its research_text values.
    """
    pending_ids: set[int] = set()
    existing_research: dict[str, str | None] = {}

    if QA_REVIEW.exists():
        with open(QA_REVIEW, encoding="utf-8") as f:
            review = json.load(f)
        for rid, entry in review.get("entries", {}).items():
            if entry.get("_approved") is False:
                pending_ids.add(int(rid))

    if MANUAL_RESEARCH_JSON.exists():
        with open(MANUAL_RESEARCH_JSON, encoding="utf-8") as f:
            existing = json.load(f)
        for rid, entry in existing.get("entries", {}).items():
            existing_research[rid] = entry.get("research_text")

    if not pending_ids:
        print("  No pending/skipped entries to research.")
        return

    entries = {}
    for rid in sorted(pending_ids):
        rid_str = str(rid)
        entries[rid_str] = {
            "ref_id": rid,
            "research_text": existing_research.get(rid_str),
            "parsed": None,
        }

    data = {
        "_meta": {
            "description": "Manual research results — fill in research_text for each ref_id, then re-run with --manual",
            "instructions": "Paste raw BibTeX or plain-text citation info in research_text. Leave as null to skip. Supported fields: title, authors, journal, year, volume, issue, pages, doi, type",
            "updated_at": datetime.now().isoformat(),
            "pending_count": len(entries),
        },
        "entries": entries,
    }

    _save_json(data, MANUAL_RESEARCH_JSON)
    print(f"  Updated → {MANUAL_RESEARCH_JSON}  ({len(entries)} entries)")


def _call_manual_parse_llm(ref_id: int, research_text: str) -> dict | None:
    """
    Call LLM to parse structured fields from arbitrary research text (BibTeX or plain text).
    Returns a dict with paper fields or None on failure.
    """
    from src.utils.constants import SYSTEM_PROMPT

    prompt = SYSTEM_PROMPT.replace("{refs_text}", f"[{ref_id}] {research_text}")
    try:
        result = get_llm_response([{"ref_id": ref_id, "raw_text": research_text}])
        if isinstance(result, list):
            result = result[0]
        if isinstance(result, dict) and result.get("ref_id") is not None:
            return result
    except Exception as e:
        print(f"  [ref #{ref_id}] LLM parse error: {e}")
    return None


def _run_manual_qa(manual_entries: list[dict]) -> list[dict]:
    """
    Run LLM QA judgment on manual research entries.
    Builds synthetic search-result-style entries and calls _call_qa_llm.
    """
    raw_map = _load_raw_map()

    synth = []
    for e in manual_entries:
        parsed = e.get("parsed") or {}
        synth.append({
            "ref_id": e["ref_id"],
            "crossref": parsed if parsed else None,
            "semantic_scholar": None,
            "mcp": None,
            "strategy_used": "manual_research",
            "llm_data": parsed,
            "qa": {},
        })

    batches = [synth[i:i + QA_BATCH_SIZE]
               for i in range(0, len(synth), QA_BATCH_SIZE)]

    all_qa = []
    for batch in batches:
        qa_batch = _call_qa_llm(batch, raw_map)
        all_qa.extend(qa_batch)

    qa_map = {int(q["ref_id"]): q for q in all_qa if q.get("ref_id") is not None}
    for r in synth:
        rid = int(r["ref_id"])
        r["qa"] = qa_map.get(rid, {
            "ref_id": rid, "confidence": "low",
            "reason": "no QA result", "agreed_fields": [], "disagreed_fields": []
        })

    # Return (all_qa, synth) — all_qa for qa_map, synth for llm_data
    return all_qa, synth


def run_manual_research():
    """
    Parse manual_research.json, run LLM extraction + QA judgment, update qa_review.json.
    """
    if not MANUAL_RESEARCH_JSON.exists():
        _init_manual_research_json()
        print("\n  Edit data/04_quality/manual_research.json and re-run with --manual")
        return

    with open(MANUAL_RESEARCH_JSON, encoding="utf-8") as f:
        research_data = json.load(f)

    to_process = []
    for rid_str, entry in research_data.get("entries", {}).items():
        rt = entry.get("research_text")
        if rt and str(rt).strip() and str(rt).strip().lower() != "null":
            to_process.append({**entry, "ref_id": int(rid_str)})

    if not to_process:
        print("  No entries with research_text filled in.")
        print("  Edit data/04_quality/manual_research.json and re-run.")
        return

    print(f"  Parsing {len(to_process)} entries via LLM (parallel, max 5 workers)...")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _parse_one(entry):
        parsed = _call_manual_parse_llm(entry["ref_id"], entry["research_text"])
        return entry["ref_id"], parsed

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_parse_one, e): e for e in to_process}
        done = 0
        for future in as_completed(futures):
            rid, parsed = future.result()
            for e in to_process:
                if e["ref_id"] == rid:
                    e["parsed"] = parsed
                    break
            done += 1
            print(f"    ref #{rid}: {'OK' if parsed else 'FAILED'}")

    print(f"\n  Running QA judgment on {len(to_process)} entries...")
    qa_raw_results, _synth = _run_manual_qa(to_process)
    # qa_raw_results: list of raw QA result dicts (ref_id + confidence + reason + agreed/disagreed_fields)
    # synth_results: list of full synth entries (ref_id + crossref + llm_data + qa subfield)
    qa_map = {int(q["ref_id"]): q for q in qa_raw_results if q.get("ref_id") is not None}

    # Update manual_research.json with parsed results (keep for audit)
    for e in to_process:
        rid_str = str(e["ref_id"])
        if rid_str in research_data["entries"]:
            research_data["entries"][rid_str]["parsed"] = e.get("parsed")
    research_data["_meta"]["updated_at"] = datetime.now().isoformat()
    _save_json(research_data, MANUAL_RESEARCH_JSON)

    # Merge into qa_review.json (the single source of truth)
    if not QA_REVIEW.exists():
        print("  ERROR: qa_review.json not found — run 'python -m src.modules.quality' first.")
        sys.exit(1)
    with open(QA_REVIEW, encoding="utf-8") as f:
        qa_review_data = json.load(f)

    approved_count = 0
    for e in to_process:
        rid = e["ref_id"]
        rid_str = str(rid)
        parsed = e.get("parsed") or {}
        qa = qa_map.get(rid, {
            "confidence": "low",
            "reason": "no QA result",
            "agreed_fields": [],
            "disagreed_fields": [],
        })

        if rid_str in qa_review_data["entries"]:
            entry = qa_review_data["entries"][rid_str]
        else:
            entry = {
                "_approved": False,
                "_reviewed_at": None,
                "_review_note": None,
                "_patch": None,
                "_decision": "pending",
                "_source": "manual_search",
                "ref_id": rid,
            }
            qa_review_data["entries"][rid_str] = entry

        # Write manual_data and qa into the existing review entry
        entry["manual_data"] = parsed
        entry.setdefault("_source", "manual_search")
        entry.setdefault("_approved", False)
        entry.setdefault("_decision", "pending")
        entry.setdefault("qa", {})
        # Update qa field directly (no separate manual_qa subfield)
        entry["qa"]["confidence"] = qa.get("confidence", "low")
        entry["qa"]["reason"] = qa.get("reason", "")
        entry["qa"]["agreed_fields"] = qa.get("agreed_fields", [])
        entry["qa"]["disagreed_fields"] = qa.get("disagreed_fields", [])

        # Auto-approve high confidence entries
        if qa.get("confidence") == "high":
            entry["_approved"] = True
            entry["_decision"] = "approved"
            entry["_reviewed_at"] = datetime.now().isoformat()
            entry["_review_note"] = "auto-approved (manual search, high confidence)"
            approved_count += 1

    # Recompute qa_review.json _meta (uses qa.confidence which was synced to manual result)
    review_entries = qa_review_data["entries"]
    qa_review_data["_meta"]["total"] = len(review_entries)
    qa_review_data["_meta"]["medium_count"] = sum(
        1 for e in review_entries.values() if e.get("qa", {}).get("confidence") == "medium"
    )
    qa_review_data["_meta"]["low_count"] = sum(
        1 for e in review_entries.values() if e.get("qa", {}).get("confidence") == "low"
    )
    # reviewed_count = entries with a user decision (approved/sked/sked by user, not auto-approved by --manual)
    qa_review_data["_meta"]["reviewed_count"] = sum(
        1 for e in review_entries.values()
        if e.get("_decision") in ("approved", "skipped", "patched")
        and e.get("_reviewed_at") is not None
    )
    qa_review_data["_meta"]["approved_count"] = sum(
        1 for e in review_entries.values() if e.get("_approved") is True
    )
    qa_review_data["_meta"]["skipped_count"] = sum(
        1 for e in review_entries.values() if e.get("_decision") == "skipped"
    )

    _save_json(qa_review_data, QA_REVIEW)
    print(f"  qa_review.json updated → {QA_REVIEW}")

    # Count from qa_raw_results (authoritative)
    high_count   = sum(1 for q in qa_raw_results if q.get("confidence") == "high")
    medium_count = sum(1 for q in qa_raw_results if q.get("confidence") == "medium")
    low_count    = sum(1 for q in qa_raw_results if q.get("confidence") == "low")
    pending_review = medium_count + low_count

    print(f"\n  QA done: high={high_count}, medium={medium_count}, low={low_count}")
    print(f"  Auto-approved (high): {approved_count}")

    # Always rebuild qa_approved.json and bib_export_report.md after --manual
    # _approved=True entries are included; _approved=False entries are excluded
    print()
    print("  Generating qa_approved.json from current qa_review.json...")
    print()
    run_approve()
    _reset_manual_research_json()


def _run_manual_review_loop(review_path: Path):
    """Reusable review loop for any review JSON path."""
    if not review_path.exists():
        print(f"ERROR: {review_path} not found.")
        sys.exit(1)

    with open(review_path, encoding="utf-8") as f:
        review_data = json.load(f)

    raw_map = _load_raw_map()
    entries = review_data["entries"]
    meta = review_data["_meta"]

    pending = sorted(
        [rid for rid, e in entries.items() if e["_approved"] is False],
        key=int
    )
    already_reviewed = sum(1 for e in entries.values() if e["_approved"] is not False)

    print(f"\n══ Manual Review: {len(pending)} entries to review "
          f"({already_reviewed} already reviewed) ══")
    print(f"  medium={meta['medium_count']}  low={meta['low_count']}")
    print(f"  Saving decisions to: {review_path}")
    print()

    idx = 0
    while idx < len(pending):
        rid = pending[idx]
        entry = entries[rid]
        _display_card(rid, entry, idx, len(pending), raw_map, meta)
        print("> ", end="", flush=True)

        raw = _getch()
        key = raw.lower()

        def _decide_local(approved: bool):
            e = review_data["entries"][rid]
            now = datetime.now().isoformat()
            e["_approved"] = approved
            e["_reviewed_at"] = now
            e["_decision"] = "approved" if approved else "skipped"
            review_data["_meta"]["reviewed_count"] += 1
            if approved:
                review_data["_meta"]["approved_count"] += 1
            else:
                review_data["_meta"]["skipped_count"] += 1
            _save_json(review_data, review_path)
            return e

        if key in ("\r", "\n", ""):
            _decide_local(True)
        elif key == "a":
            _decide_local(True)
        elif key == "s":
            _decide_local(False)
        elif key == "e":
            _do_patch(review_data, rid)
            with open(review_path, encoding="utf-8") as f:
                review_data = json.load(f)
            entries = review_data["entries"]
            pending = sorted([r for r, e in entries.items() if e["_approved"] is False], key=int)
            continue
        elif key == "p":
            try:
                import fcntl as _fcntl, os as _os2
                fd = sys.stdin.fileno()
                fl = _fcntl.fcntl(fd, _fcntl.F_GETFL)
                _fcntl.fcntl(fd, _fcntl.F_SETFL, fl | _os2.O_NONBLOCK)
                try:
                    while True:
                        sys.stdin.read(1)
                except Exception:
                    pass
                _fcntl.fcntl(fd, _fcntl.F_SETFL, fl)
            except Exception:
                pass
            line = sys.stdin.readline().strip()
            ids = _parse_approve_range(line, pending)
            for target_rid in ids:
                if target_rid in entries and entries[target_rid]["_approved"] is False:
                    _decide_local(True)
                    with open(review_path, encoding="utf-8") as f:
                        review_data = json.load(f)
                    entries = review_data["entries"]
            pending = sorted([r for r, e in entries.items() if e["_approved"] is False], key=int)
            continue
        elif key == "d":
            for r in pending[idx:]:
                if entries[r]["_approved"] is False:
                    _decide_local(False)
            break
        elif key == "q":
            _save_json(review_data, review_path)
            print("  Saved progress.")
            sys.exit(0)
        else:
            idx -= 1
        idx += 1

    with open(review_path, encoding="utf-8") as f:
        final_data = json.load(f)
    final_meta = final_data["_meta"]
    print()
    print("══ Manual Review complete ══")
    print(f"  Reviewed:  {final_meta['reviewed_count']}")
    print(f"  Approved:  {final_meta['approved_count']}")
    print(f"  Skipped:   {final_meta['skipped_count']}")

    # Always call run_approve() when operating on qa_review.json
    # Safe to call even if review was partial — it rebuilds qa_approved.json from current _approved state
    if review_path == QA_REVIEW:
        print()
        print("  Generating qa_approved.json from current qa_review.json...")
        print()
        run_approve()
        _reset_manual_research_json()


def run_manual_review():
    """Interactive review for manual_review.json entries."""
    _run_manual_review_loop(MANUAL_REVIEW_JSON)


def run_manual_approve():
    """Merge approved manual_review.json entries into qa_approved.json."""
    if not MANUAL_REVIEW_JSON.exists():
        print(f"ERROR: {MANUAL_REVIEW_JSON} not found — run with --manual first.")
        sys.exit(1)

    with open(MANUAL_REVIEW_JSON, encoding="utf-8") as f:
        manual_data = json.load(f)

    existing_approved: list[dict] = []
    if QA_APPROVED.exists():
        with open(QA_APPROVED, encoding="utf-8") as f:
            existing_approved = json.load(f)

    approved_map = {int(e["ref_id"]): e for e in existing_approved}

    for rid_str, entry in manual_data["entries"].items():
        if entry.get("_approved") is not True:
            continue
        rid = int(rid_str)
        base = {k: v for k, v in entry.items() if not k.startswith("_")}
        base["_approved_via"] = entry["qa"].get("confidence", "manual")
        base["_decision"] = "manual"
        base["_source"] = "manual_review"
        patch = entry.get("_patch")
        if patch:
            base["_patch"] = patch
        base["_review_note"] = entry.get("_review_note", "")
        approved_map[rid] = base  # overwrite any previous entry with same ref_id

    result = sorted(approved_map.values(), key=lambda x: int(x["ref_id"]))
    _save_json(result, QA_APPROVED)

    manual_approved = sum(1 for e in manual_data["entries"].values() if e.get("_approved") is True)
    print(f"  Merged {manual_approved} manual entries into qa_approved.json")
    print(f"  qa_approved.json → {QA_APPROVED}  ({len(result)} total entries)")

# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    for _d in [STAGE_RAW, STAGE_SEARCH, STAGE_QUAL, STAGE_EXP]:
        _d.mkdir(parents=True, exist_ok=True)

    if "--manual-approve" in sys.argv:
        run_manual_approve()
    elif "--manual-review" in sys.argv:
        run_manual_review()
    elif "--manual" in sys.argv:
        run_manual_research()
    elif "--approve" in sys.argv:
        run_approve()
    elif "--review" in sys.argv:
        run_review()
    else:
        run_qa()


if __name__ == "__main__":
    main()
