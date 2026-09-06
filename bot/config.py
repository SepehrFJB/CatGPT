from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///catgpt.sqlite3"


class SettingsError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    deepseek_api_key: str = field(repr=False)
    deepseek_base_url: str
    deepseek_model: str
    admin_ids: frozenset[int]
    support_url: str
    free_daily_limit: int
    plus_daily_limit: int
    plus_stars_price: int
    plus_period_seconds: int
    max_input_chars: int
    max_output_tokens: int
    database_url: str
    group_welcome_photo: str
    deepseek_vision_detail: str
    deepseek_thinking_mode: str
    deepseek_thinking_effort: str

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "Settings":
        load_dotenv(env_file)

        return cls(
            bot_token=_required("BOT_TOKEN"),
            deepseek_api_key=_required("DEEPSEEK_API_KEY"),
            deepseek_base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            deepseek_model=_env("DEEPSEEK_MODEL", "deepseek-v4-flash-vision-exp"),
            deepseek_vision_detail=_env("DEEPSEEK_VISION_DETAIL", "low"),
            deepseek_thinking_mode=_env("DEEPSEEK_THINKING_MODE", "disabled"),
            deepseek_thinking_effort=_env("DEEPSEEK_THINKING_EFFORT", "low"),
            admin_ids=_parse_admin_ids(_env("ADMIN_IDS", "")),
            support_url=_env("SUPPORT_URL", ""),
            free_daily_limit=_positive_int("FREE_DAILY_LIMIT", 10),
            plus_daily_limit=_positive_int("PLUS_DAILY_LIMIT", 50),
            plus_stars_price=_positive_int("PLUS_STARS_PRICE", 100),
            plus_period_seconds=_positive_int("PLUS_PERIOD_SECONDS", 2_592_000),
            max_input_chars=_positive_int("MAX_INPUT_CHARS", 1_000),
            max_output_tokens=_positive_int("MAX_OUTPUT_TOKENS", 800),
            database_url=_env("DATABASE_URL", DEFAULT_DATABASE_URL),
            group_welcome_photo=_env("GROUP_WELCOME_PHOTO", ""),
        )

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


def sqlite_path_from_url(database_url: str) -> str:
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    for prefix in prefixes:
        if database_url.startswith(prefix):
            path = database_url.removeprefix(prefix)
            return path or ":memory:"
    if database_url == ":memory:":
        return database_url
    raise SettingsError("Only sqlite+aiosqlite:/// URLs are supported.")


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _required(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise SettingsError(f"Missing required environment variable: {name}")
    return value.strip()


def _positive_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise SettingsError(f"{name} must be greater than zero.")
    return value


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError as exc:
            raise SettingsError("ADMIN_IDS must be a comma-separated list of integers.") from exc
    return frozenset(ids)
