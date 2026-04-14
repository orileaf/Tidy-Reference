import json
import re
import time

import requests

from src.config import get
from src.utils.constants import LLM_TIMEOUT, SYSTEM_PROMPT

# ── Helpers ──────────────────────────────────────────────────────────────────────

def _strip_json_fences(text: str) -> str:
    """
    Strip thinking/reasoning blocks before the JSON.
    Uses hex escapes for the XML-like tags so the file itself is valid Python
    regardless of how the repo viewer interprets <think> / ).
    """
    if not text:
        return ""

    # <think> → \xe2\x90\x9a\xef\xb8\x8f  (UTF-8 of U+2061..U+2068 "annotation" chars)
    # ) → \xe2\x90\x9b\xef\xb8\x8f
    _think_start = b"\xe2\x90\x9a\xef\xb8\x8f".decode()   # )
    _think_end   = b"\xe2\x90\x9b\xef\xb8\x8f".decode()   # )
    text = re.sub(
        re.escape(_think_start) + r".*?" + re.escape(_think_end),
        "", text, flags=re.DOTALL
    )

    # Strip markdown fences
    text = re.sub(r"^\s*```(?:json)?", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip()

    # Find first '[' or '{' that marks the JSON payload start
    idx_a = text.find("[")
    idx_o = text.find("{")
    candidates = [i for i in [idx_a, idx_o] if i >= 0]
    if not candidates:
        return ""
    return text[min(candidates):]


# ── OpenAI SDK ───────────────────────────────────────────────────────────────────

def _get_openai_client():
    """
    Build an openai.OpenAI client using DASHSCOPE_API_KEY (Ali Bailian / Qwen)
    with base_url from config, falling back to OPENAI_API_KEY.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "The 'openai' package is required. Install it with:\n"
            "  pip install openai"
        )

    api_key = get("DASHSCOPE_API_KEY") or get("OPENAI_API_KEY", "")
    base_url = get("OPENAI_BASE_URL", "").rstrip("/")

    if not api_key:
        raise RuntimeError(
            "No API key found. Set DASHSCOPE_API_KEY (Ali Bailian / Qwen) "
            "or OPENAI_API_KEY in config.env"
        )

    kwargs = {"api_key": api_key, "timeout": LLM_TIMEOUT}
    if base_url:
        kwargs["base_url"] = base_url

    return OpenAI(**kwargs)


def _call_via_sdk(client, model: str, messages: list[dict]) -> list[dict]:
    """
    Call the LLM via openai.ChatCompletion.create and return the parsed
    JSON list (or dict wrapped in {"references": ...}).
    Raises on network / API errors so the caller can retry.

    Note: qwen3 series models require enable_thinking=False for non-streaming
    calls; this is injected via extra_body.
    """
    extra_body = {}
    if model.startswith("qwen3"):
        extra_body["enable_thinking"] = False

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.0,
        extra_body=extra_body if extra_body else None,
    )
    content = resp.choices[0].message.content or ""
    content = _strip_json_fences(content)
    parsed = json.loads(content)
    return parsed if isinstance(parsed, list) else parsed.get("references", parsed)


# ── Public API ──────────────────────────────────────────────────────────────────

def get_llm_response(
    refs_batch: list[dict],
    max_retries: int = 2,
    *,
    system: str | None = None,
    user: str | None = None,
) -> list[dict]:
    """
    Send a batch of raw references to the LLM via OpenAI-compatible API
    (DashScope / Qwen, OpenAI, or compatible endpoint).

    Retries on JSON-parse or network error up to max_retries times.

    When system+user are provided they are used as-is (bypassing
    SYSTEM_PROMPT and the default refs_batch formatting) — used by
    quality.py for QA judgment.
    """
    client = _get_openai_client()
    model = get("OPENAI_MODEL", "qwen-plus")
    errors = []

    for attempt in range(max_retries):
        try:
            if system is not None and user is not None:
                # Custom prompt path (quality.py QA judgment)
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": user})
                parsed = _call_via_sdk(client, model, messages)
            else:
                # Default path: SYSTEM_PROMPT + refs_batch
                refs_text = "\n".join(
                    f"[{r['ref_id']}] {r['raw_text']}" for r in refs_batch
                )
                prompt = SYSTEM_PROMPT.replace("{refs_text}", refs_text)
                messages = [{"role": "user", "content": prompt}]
                parsed = _call_via_sdk(client, model, messages)

            parsed = _post_validate_llm(parsed)
            return parsed

        except json.JSONDecodeError as e:
            errors.append(f"JSON parse error (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
        except Exception as e:
            errors.append(f"API error (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)

    print(f"  WARNING: LLM JSON parse failed after {max_retries} attempts: {errors[-1]}")
    skeletons = []
    for r in refs_batch:
        skeletons.append({
            "ref_id": r["ref_id"],
            "authors": None, "title": None, "journal": None,
            "year": None, "volume": None, "pages": None, "doi": None,
            "status": "not_found", "field_confidence": {},
        })
    return skeletons


def _post_validate_llm(parsed: list[dict]) -> list[dict]:
    """
    Validate LLM output. Downgrade status to 'partial' if:
    - DOI format is invalid
    - A field marked as 'null' in field_confidence has a non-null value
    """
    doi_pattern = re.compile(r"^10\.\d+/")

    for entry in parsed:
        flags = []
        doi = entry.get("doi")
        if doi and not doi_pattern.match(str(doi)):
            entry["doi"] = None
            flags.append("bad_doi_format")

        fc = entry.get("field_confidence", {})
        if isinstance(fc, dict):
            for field, confidence in fc.items():
                if confidence == "null" and entry.get(field) is not None:
                    entry[field] = None
                    flags.append(f"fabricated_{field}")

        guessed_fields = [k for k, v in fc.items() if v == "null"] if isinstance(fc, dict) else []
        core_missing = any(f in guessed_fields for f in ["title", "authors", "year"])
        if core_missing and entry.get("status") == "found":
            entry["status"] = "partial"

        if flags:
            entry["qc_flags"] = flags

    return parsed
