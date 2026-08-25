from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from openai import APITimeoutError, AsyncOpenAI

from bot.config import Settings
from bot.texts import CATGPT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserInput:
    text: str
    image_base64: str | None = None
    image_mime_type: str = "image/jpeg"
    image_detail: str | None = None


class AIProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        likely_image_unsupported: bool = False,
        is_timeout: bool = False,
    ):
        super().__init__(message)
        self.likely_image_unsupported = likely_image_unsupported
        self.is_timeout = is_timeout


class CatGPTAI:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http_client = httpx.AsyncClient(http2=True)
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            http_client=self.http_client,
        )

    async def close(self) -> None:
        await self.client.close()

    async def stream_chat(
        self,
        telegram_user_id: int,
        user_input: UserInput,
        last_turn: tuple[str, str] | None,
        context_messages: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        messages = build_messages(user_input, last_turn, context_messages)
        try:
            request_kwargs = self._request_kwargs(telegram_user_id, user_input, messages)
            stream = await self.client.chat.completions.create(**request_kwargs)
            async for chunk in stream:
                usage = extract_usage(chunk)
                if usage is not None:
                    log_cache_usage(telegram_user_id, usage)

                delta = extract_chat_delta(chunk)
                if delta:
                    yield delta
        except Exception as exc:  # noqa: BLE001 - SDK/provider exceptions vary by compatible endpoint.
            logger.exception("AI provider request failed")
            exc_str = str(exc).lower()
            likely_image_unsupported = bool(
                user_input.image_base64
                and any(
                    err in exc_str
                    for err in (
                        "image",
                        "vision",
                        "multimodal",
                        "unsupported",
                        "400",
                    )
                )
            )
            raise AIProviderError(
                "AI provider request failed",
                likely_image_unsupported=likely_image_unsupported,
                is_timeout=_is_timeout_error(exc),
            ) from exc

    def _request_kwargs(
        self,
        telegram_user_id: int,
        user_input: UserInput,
        messages: list[dict],
    ) -> dict:
        thinking_mode = self.settings.deepseek_thinking_mode.strip().lower()
        is_thinking_enabled = thinking_mode in ("enabled", "enable", "true", "1", "on")

        extra_body: dict[str, Any] = {
            "user_id": str(telegram_user_id),
            "thinking": {"type": "enabled" if is_thinking_enabled else "disabled"},
        }

        kwargs: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "max_tokens": self.settings.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "tool_choice": "none",
            "extra_body": extra_body,
        }

        if is_thinking_enabled and self.settings.deepseek_thinking_effort:
            kwargs["reasoning_effort"] = self.settings.deepseek_thinking_effort.strip().lower()

        return kwargs


def build_messages(
    user_input: UserInput,
    last_turn: tuple[str, str] | None,
    context_messages: list[dict[str, str]] | None = None,
) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": CATGPT_SYSTEM_PROMPT}]
    if last_turn is not None:
        last_user, last_assistant = last_turn
        messages.extend(
            [
                {"role": "user", "content": last_user},
                {"role": "assistant", "content": last_assistant},
            ]
        )
    if context_messages:
        messages.extend(context_messages)
    messages.append({"role": "user", "content": build_user_content(user_input)})
    return messages


def build_user_content(user_input: UserInput) -> str | list[dict]:
    if not user_input.image_base64:
        return user_input.text

    content: list[dict] = []
    text = user_input.text.strip()
    if text:
        content.append({"type": "text", "text": text})
    else:
        content.append({"type": "text", "text": "Describe this image in detail or answer questions about it."})

    mime = user_input.image_mime_type or "image/jpeg"
    image_url_dict: dict[str, str] = {
        "url": f"data:{mime};base64,{user_input.image_base64}",
    }
    if user_input.image_detail:
        image_url_dict["detail"] = user_input.image_detail

    content.append(
        {
            "type": "image_url",
            "image_url": image_url_dict,
        }
    )
    return content


def extract_chat_delta(chunk: object) -> str:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""
    first = choices[0]
    delta = getattr(first, "delta", None)
    if delta is None:
        return ""
    content = getattr(delta, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(delta, dict):
        value = delta.get("content")
        return value if isinstance(value, str) else ""
    return ""


def extract_usage(chunk: object) -> dict | None:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage

    fields = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )
    return {
        field: getattr(usage, field)
        for field in fields
        if getattr(usage, field, None) is not None
    }


def log_cache_usage(telegram_user_id: int, usage: dict) -> None:
    hit_tokens = usage.get("prompt_cache_hit_tokens")
    miss_tokens = usage.get("prompt_cache_miss_tokens")
    if hit_tokens is None and miss_tokens is None:
        logger.info("DeepSeek usage user_id=%s usage=%s", telegram_user_id, usage)
        return

    logger.info(
        "DeepSeek prompt cache user_id=%s hit_tokens=%s miss_tokens=%s total_tokens=%s",
        telegram_user_id,
        hit_tokens or 0,
        miss_tokens or 0,
        usage.get("total_tokens", 0),
    )


def _is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, (APITimeoutError, TimeoutError))
