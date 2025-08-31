from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # Telegram
    bot_token: str = Field(..., alias='BOT_TOKEN')

    # Database
    database_url: str = Field(..., alias='DATABASE_URL')  # e.g., postgresql+asyncpg://user:pass@host:port/db

    # OpenRouter
    openrouter_api_key: str | None = Field(None, alias='OPENROUTER_API_KEY')
    openrouter_model: str = Field("deepseek/deepseek-chat", alias='OPENROUTER_MODEL')

    # App
    default_timezone: str = Field("Europe/Moscow", alias='DEFAULT_TZ')
    default_checkin_time: str = Field("18:00", alias='DEFAULT_CHECKIN_TIME')  # HH:MM 24h
    crisis_locale: str = Field("ru", alias='CRISIS_LOCALE')


settings = Settings()  # will read from .env
