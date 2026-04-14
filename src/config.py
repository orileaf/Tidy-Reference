#!/usr/bin/env python3
"""
src/config.py — Load config.env and expose config dict.
Place in src/ so it can be imported as `from src.config import config`.
"""

import os
import sys
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "config.env"
_CONFIG_LOADED = False
_CONFIG = {}


def _load():
    global _CONFIG, _CONFIG_LOADED
    if _CONFIG_LOADED:
        return _CONFIG
    _CONFIG_LOADED = True

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    _CONFIG[key] = value
                    if key not in os.environ:
                        os.environ[key] = value

    if not _CONFIG.get("ANTHROPIC_API_KEY") and not _CONFIG.get("OPENAI_API_KEY") and not _CONFIG.get("DASHSCOPE_API_KEY"):
        example = CONFIG_FILE.parent / "config.env.example"
        print(
            f"WARNING: No API keys found in {CONFIG_FILE}\n"
            f"Copy {example} → {CONFIG_FILE} and fill in your credentials.",
            file=sys.stderr,
        )

    return _CONFIG


config = _load()


def get(key: str, default=None):
    """Get a config value, falling back to environment variable."""
    return _CONFIG.get(key) or os.environ.get(key, default)
