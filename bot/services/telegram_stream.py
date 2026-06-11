from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from html import escape

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

logger = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 4096
DRAFT_LOADING_MESSAGE = "\u2060"


class TelegramDraftStreamer:
    def __init__(
        self,
        bot: object,
        chat_id: int,
        *,
        draft_id: int,
        min_interval_seconds: float = 2.5,
        min_chars: int = 120,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.draft_id = draft_id
        self.min_interval_seconds = min_interval_seconds
        self.min_chars = min_chars
        self.enabled = True
        self._last_text = ""
        self._last_sent_at = 0.0
        self._retry_after_until = 0.0
        self._lock = asyncio.Lock()

    async def start(self) -> bool:
        return await self._send_draft(DRAFT_LOADING_MESSAGE, raw_text="")

    async def push(self, text: str, *, force: bool = False) -> bool:
        if not self.enabled:
            return False
        now = time.monotonic()
        if now < self._retry_after_until:
            return True
        enough_time = now - self._last_sent_at >= self.min_interval_seconds
        enough_chars = len(text) - len(self._last_text) >= self.min_chars
        if not force and not (enough_time and enough_chars):
            return True
        draft_text, parse_mode = format_draft_text(text)
        return await self._send_draft(draft_text, raw_text=text, parse_mode=parse_mode)

    async def _send_draft(
        self,
        text: str,
        *,
        raw_text: str | None = None,
        parse_mode: str | None = None,
    ) -> bool:
        async with self._lock:
            if not self.enabled:
                return False
            try:
                method = getattr(self.bot, "send_message_draft")
                kwargs = {
                    "chat_id": self.chat_id,
                    "draft_id": self.draft_id,
                    "text": text,
                }
                if parse_mode is not None:
                    kwargs["parse_mode"] = parse_mode
                await method(**kwargs)
                self._last_text = raw_text if raw_text is not None else text
                self._last_sent_at = time.monotonic()
                return True
            except TelegramRetryAfter as exc:
                self._retry_after_until = time.monotonic() + float(exc.retry_after) + 0.25
                logger.warning(
                    "Telegram draft streaming is rate-limited; retrying after %ss",
                    exc.retry_after,
                )
                return True
            except TelegramBadRequest as exc:
                if "TEXTDRAFT_PEER_INVALID" in str(exc):
                    logger.warning("Telegram draft streaming is not available for this chat")
                else:
                    logger.warning("Telegram draft streaming was rejected: %s", exc)
                self.enabled = False
                return False
            except Exception:  # noqa: BLE001 - Telegram clients/servers may not support drafts.
                logger.exception("Telegram draft streaming failed; falling back to regular messages")
                self.enabled = False
                return False


async def send_final_text(
    bot: object,
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
) -> list[object]:
    sent_messages: list[object] = []
    for chunk, parse_mode in split_final_text(text):
        kwargs = {"chat_id": chat_id, "text": chunk}
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
            kwargs["allow_sending_without_reply"] = True
        sent_messages.append(await bot.send_message(**kwargs))
    return sent_messages


def split_final_text(text: str) -> Sequence[tuple[str, str | None]]:
    text = cleanup_markdown_artifacts(text)
    if not has_code_fence(text):
        if has_inline_markdown(text):
            return [(chunk, "HTML") for chunk in split_telegram_text(format_inline_markdown_as_html(text))]
        return [(chunk, None) for chunk in split_telegram_text(text)]
    return format_code_fences_as_html_chunks(text)


def format_draft_text(text: str) -> tuple[str, str | None]:
    text = cleanup_markdown_artifacts(text)
    if not has_code_fence(text):
        if has_inline_markdown(text):
            return format_inline_markdown_as_html(text)[:TELEGRAM_TEXT_LIMIT], "HTML"
        return text[:TELEGRAM_TEXT_LIMIT], None

    chunks = format_code_fences_as_html_chunks(text)
    if not chunks:
        return text[:TELEGRAM_TEXT_LIMIT], None
    chunk, parse_mode = chunks[0]
    return chunk[:TELEGRAM_TEXT_LIMIT], parse_mode


def split_telegram_text(text: str) -> Sequence[str]:
    if not text:
        return []

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= TELEGRAM_TEXT_LIMIT:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, TELEGRAM_TEXT_LIMIT)
        if split_at < TELEGRAM_TEXT_LIMIT // 2:
            split_at = remaining.rfind(" ", 0, TELEGRAM_TEXT_LIMIT)
        if split_at < TELEGRAM_TEXT_LIMIT // 2:
            split_at = TELEGRAM_TEXT_LIMIT
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks


def cleanup_markdown_artifacts(text: str) -> str:
    lines: list[str] = []
    quote_lines: list[str] = []
    in_code_block = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            flush_blockquote(quote_lines, lines)
            lines.append(line)
            in_code_block = not in_code_block
            continue
        if in_code_block:
            lines.append(line)
            continue

        quote_match = re.match(r"^\s{0,3}>\s?(.*)$", line)
        if quote_match:
            quote_lines.append(quote_match.group(1))
            continue
        flush_blockquote(quote_lines, lines)

        heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+)$", line)
        if heading_match:
            lines.append(f"**{heading_match.group(1).strip()}**")
            continue
        lines.append(line)
    flush_blockquote(quote_lines, lines)
    return "\n".join(lines)


def flush_blockquote(quote_lines: list[str], output_lines: list[str]) -> None:
    if not quote_lines:
        return
    quote_text = "\n".join(quote_lines).strip()
    if quote_text:
        output_lines.append(f"<blockquote>{quote_text}</blockquote>")
    quote_lines.clear()


def has_inline_markdown(text: str) -> bool:
    return (
        "`" in text
        or "**" in text
        or "__" in text
        or "~~" in text
        or "<blockquote>" in text
        or bool(re.search(r"\[[^\]\n]+\]\([^) \n]+(?: [^)]+)?\)", text))
    )


def format_inline_markdown_as_html(text: str) -> str:
    if "<blockquote>" in text:
        return format_inline_markdown_preserving_blockquotes(text)

    parts: list[str] = []
    position = 0
    while position < len(text):
        code_start = text.find("`", position)
        bold_start = find_next_inline_marker(text, position)
        next_start = min_existing(code_start, bold_start)
        if next_start < 0:
            parts.append(escape(text[position:]))
            break

        if next_start > position:
            parts.append(escape(text[position:next_start]))

        link_result = parse_markdown_link(text, next_start)
        if link_result is not None:
            rendered, position = link_result
            parts.append(rendered)
            continue

        if next_start == code_start:
            end = text.find("`", code_start + 1)
            if end < 0 or "\n" in text[code_start + 1 : end]:
                parts.append(escape(text[code_start]))
                position = code_start + 1
                continue
            parts.append(f"<code>{escape(text[code_start + 1:end])}</code>")
            position = end + 1
            continue

        if text.startswith("~~", next_start):
            end = text.find("~~", next_start + 2)
            if end < 0 or "\n" in text[next_start + 2 : end]:
                parts.append("~~")
                position = next_start + 2
                continue
            inner = format_inline_markdown_as_html(text[next_start + 2 : end])
            parts.append(f"<s>{inner}</s>")
            position = end + 2
            continue

        marker = text[bold_start : bold_start + 2]
        end = text.find(marker, bold_start + 2)
        if end < 0 or "\n" in text[bold_start + 2 : end]:
            parts.append(escape(marker))
            position = bold_start + 2
            continue
        inner = format_inline_markdown_as_html(text[bold_start + 2 : end])
        parts.append(f"<b>{inner}</b>")
        position = end + 2

    return "".join(parts)


def format_inline_markdown_preserving_blockquotes(text: str) -> str:
    parts: list[str] = []
    position = 0
    pattern = re.compile(r"<blockquote>(.*?)</blockquote>", re.DOTALL)
    for match in pattern.finditer(text):
        if match.start() > position:
            parts.append(format_inline_markdown_as_html(text[position : match.start()]))
        quote_body = format_inline_markdown_as_html(match.group(1))
        parts.append(f"<blockquote>{quote_body}</blockquote>")
        position = match.end()
    if position < len(text):
        parts.append(format_inline_markdown_as_html(text[position:]))
    return "".join(parts)


def find_next_inline_marker(text: str, start: int) -> int:
    candidates = [
        index
        for index in (
            text.find("**", start),
            text.find("__", start),
            text.find("~~", start),
            find_next_markdown_link(text, start),
        )
        if index >= 0
    ]
    return min(candidates) if candidates else -1


def find_next_markdown_link(text: str, start: int) -> int:
    match = re.search(r"\[[^\]\n]+\]\([^) \n]+(?: [^)]+)?\)", text[start:])
    return start + match.start() if match else -1


def parse_markdown_link(text: str, start: int) -> tuple[str, int] | None:
    match = re.match(r"\[([^\]\n]+)\]\(([^) \n]+)(?: [^)]+)?\)", text[start:])
    if match is None:
        return None
    label = format_inline_markdown_as_html(match.group(1))
    url = escape(match.group(2), quote=True)
    return f'<a href="{url}">{label}</a>', start + match.end()


def min_existing(*values: int) -> int:
    candidates = [value for value in values if value >= 0]
    return min(candidates) if candidates else -1


def has_code_fence(text: str) -> bool:
    return "```" in text


def format_code_fences_as_html_chunks(text: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    position = 0

    while position < len(text):
        opening = text.find("```", position)
        if opening < 0:
            append_html_text_chunks(chunks, text[position:])
            break
        if opening > position:
            append_html_text_chunks(chunks, text[position:opening])

        header_start = opening + 3
        line_end = find_line_end(text, header_start)
        header = text[header_start:line_end].strip()
        language = normalize_code_language(header)
        code_start = skip_line_break(text, line_end)
        closing = text.find("```", code_start)
        if closing < 0:
            code = text[code_start:].strip("\n\r")
            append_html_code_chunks(chunks, code, language)
            break

        code = text[code_start:closing].strip("\n\r")
        append_html_code_chunks(chunks, code, language)
        position = closing + 3

    return coalesce_html_chunks(chunks or [(escape(text), "HTML")])


def find_line_end(text: str, start: int) -> int:
    cr = text.find("\r", start)
    lf = text.find("\n", start)
    candidates = [index for index in (cr, lf) if index >= 0]
    return min(candidates) if candidates else len(text)


def skip_line_break(text: str, position: int) -> int:
    if text.startswith("\r\n", position):
        return position + 2
    if position < len(text) and text[position] in {"\r", "\n"}:
        return position + 1
    return position


def append_html_text_chunks(chunks: list[tuple[str, str]], text: str) -> None:
    escaped = format_inline_markdown_as_html(text)
    for chunk in split_telegram_text(escaped):
        if chunk:
            chunks.append((chunk, "HTML"))


def append_html_code_chunks(chunks: list[tuple[str, str]], code: str, language: str) -> None:
    prefix = f'<pre><code class="language-{language}">'
    suffix = "</code></pre>"
    max_code_length = TELEGRAM_TEXT_LIMIT - len(prefix) - len(suffix)
    remaining = code

    if not remaining:
        chunks.append((f"{prefix}{suffix}", "HTML"))
        return

    while remaining:
        piece, remaining = split_raw_for_escaped_limit(remaining, max_code_length)
        chunks.append((f"{prefix}{escape(piece)}{suffix}", "HTML"))


def split_raw_for_escaped_limit(text: str, escaped_limit: int) -> tuple[str, str]:
    if len(escape(text)) <= escaped_limit:
        return text, ""

    low = 1
    high = len(text)
    best = 1
    while low <= high:
        mid = (low + high) // 2
        if len(escape(text[:mid])) <= escaped_limit:
            best = mid
            low = mid + 1
        else:
            high = mid - 1

    split_at = text.rfind("\n", 0, best)
    if split_at > best // 2:
        best = split_at + 1
    return text[:best], text[best:]


def normalize_code_language(language: str) -> str:
    value = language.strip().lower()
    if not value:
        return "plaintext"
    return re.sub(r"[^a-z0-9_+#.-]", "", value) or "plaintext"


def coalesce_html_chunks(chunks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    current = ""
    for chunk, parse_mode in chunks:
        if parse_mode != "HTML":
            if current:
                merged.append((current, "HTML"))
                current = ""
            merged.append((chunk, parse_mode))
            continue
        if not current:
            current = chunk
            continue
        if len(current) + len(chunk) <= TELEGRAM_TEXT_LIMIT:
            current += chunk
            continue
        merged.append((current, "HTML"))
        current = chunk
    if current:
        merged.append((current, "HTML"))
    return merged
