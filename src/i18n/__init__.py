from __future__ import annotations
import json
from pathlib import Path

_LOCALES = {}
_BASE = Path(__file__).parent
for loc in ("ru", "en"):
    with open(_BASE / f"strings_{loc}.json", "r", encoding="utf-8") as f:
        _LOCALES[loc] = json.load(f)

def t(key: str, locale: str = "ru") -> str:
    data = _LOCALES.get(locale) or _LOCALES["ru"]
    return data.get(key, key)
