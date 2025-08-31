from __future__ import annotations
import httpx
from typing import Optional
from .config import settings

SAFETY_SYSTEM_PROMPT = (
    "You are a professional, empathetic mental health coach. \n"
    "Goals: help self-reflection, awareness, and daily check-ins. \n"
    "Rules: do NOT diagnose; avoid clinical terms; never give harmful or high-risk advice; \n"
    "encourage seeking professional help if needed; keep responses concise but supportive; \n"
    "ask clarifying questions only when necessary. \n"
)

CRISIS_KEYWORDS = [
    "суицид", "покончу", "умереть", "самоповреж", "self-harm", "suicide", "kill myself",
    "не хочу жить", "не вижу смысла",
]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

async def analyze_checkin(text: str, locale: str = "ru") -> str:
    if not settings.openrouter_api_key:
        # Fallback simple deterministic response
        return (
            "Краткий разбор (без LLM): я вижу важные моменты в ваших ответах. \n"
            "Подумайте, что помогло сегодня, и что можно сделать завтра (сон, отдых, поддержка)."
            if locale == "ru" else
            "Brief analysis (no LLM): I see key points in your input. Consider what helped today and what to try tomorrow (sleep, rest, support)."
        )

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }

    messages = [
        {"role": "system", "content": SAFETY_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]

    payload = {
        "model": settings.openrouter_model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 400,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not content:
            return "Не удалось получить ответ от модели." if locale == "ru" else "Failed to get model response."
        return content


def detect_crisis(text: str) -> bool:
    lower = text.lower()
    return any(k in lower for k in CRISIS_KEYWORDS)
