from __future__ import annotations

from config import settings

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

import httpx
from httpx import HTTPStatusError, RequestError

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def analyze_checkin(text: str, locale: str = "ru") -> str:
    if not settings.openrouter_api_key:
        return ("Краткий разбор (без LLM): я вижу важные моменты в ваших ответах.\n"
                "Подумайте, что помогло сегодня, и что можно сделать завтра (сон, отдых, поддержка)."
                if locale == "ru" else
                "Brief analysis (no LLM): I see key points in your input. Consider what helped today and what to try tomorrow (sleep, rest, support).")

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Fyukon/mindCheckBot",
        "X-Title": "MindCheckBot",
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SAFETY_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.7,
        "max_tokens": 400,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            return content or (
                "Не удалось получить ответ от модели." if locale == "ru" else "Failed to get model response.")
    except HTTPStatusError as e:
        if e.response is not None and e.response.status_code in (402, 403, 429):
            return (
                "Краткий разбор (без LLM): сервис недоступен." if locale == "ru" else "Brief analysis (no LLM): service unavailable.")
        raise
    except RequestError:
        return (
            "Краткий разбор (без LLM): сеть недоступна." if locale == "ru" else "Brief analysis (no LLM): network error.")


def detect_crisis(text: str) -> bool:
    lower = text.lower()
    return any(k in lower for k in CRISIS_KEYWORDS)
