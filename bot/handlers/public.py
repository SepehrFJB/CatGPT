from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from secrets import randbelow
from uuid import uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart, Filter
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    LabeledPrice,
    Message,
    ReplyKeyboardRemove,
)

from bot.config import Settings
from bot.database import CatGPTStorage, format_timestamp
from bot.keyboards import admin_keyboard, force_join_check_keyboard, plus_payment_keyboard
from bot.services.ai import AIProviderError, CatGPTAI, UserInput
from bot.services.payments import (
    PLUS_CURRENCY,
    PLUS_DESCRIPTION,
    PLUS_PRICE_LABEL,
    PLUS_TITLE,
    make_plus_payload,
)
from bot.services.telegram_stream import TelegramDraftStreamer, send_final_text, split_final_text
from bot.texts import (
    BLOCKED_ACCESS_MESSAGE,
    EMPTY_RESPONSE_MESSAGE,
    FORCE_JOIN_MISSING_MESSAGE,
    FORCE_JOIN_OK_MESSAGE,
    FORCE_JOIN_REQUIRED_MESSAGE,
    GENERIC_AI_ERROR_MESSAGE,
    GROUP_WELCOME_MESSAGE,
    IMAGE_PROVIDER_ERROR_MESSAGE,
    INPUT_TOO_LONG_MESSAGE,
    QUOTA_EXCEEDED_MESSAGE,
    START_MESSAGE,
    THINKING_MESSAGE,
    AI_TIMEOUT_MESSAGE,
    PHOTO_DISABLED_MESSAGE,
    UNSUPPORTED_ATTACHMENT_MESSAGE,
    format_account_message,
)

logger = logging.getLogger(__name__)
router = Router(name="public")


INLINE_AI_TIMEOUT_SECONDS = 8.0


class TextChatFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        return await should_handle_text(message)


class NonTextChatFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        return await should_handle_non_text(message)


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    await app_storage.upsert_user(message.from_user)
    await app_storage.upsert_group_chat(message.chat)
    reply_markup = ReplyKeyboardRemove()
    if await is_bot_admin(message, settings, app_storage):
        reply_markup = admin_keyboard()
    if not await ensure_user_access(message, settings, app_storage):
        return
    await message.answer(START_MESSAGE, reply_markup=reply_markup)


@router.message(Command("account"), F.chat.type == "private")
@router.message(F.text == "👤 My Account", F.chat.type == "private")
async def account(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if message.from_user is None:
        return
    await app_storage.upsert_user(message.from_user)
    await app_storage.upsert_group_chat(message.chat)
    if not await ensure_user_access(message, settings, app_storage):
        return
    is_admin = await is_bot_admin(message, settings, app_storage)
    snapshot = await app_storage.account_snapshot(
        telegram_id=message.from_user.id,
        free_limit=settings.free_daily_limit,
        plus_limit=settings.plus_daily_limit,
        is_admin=is_admin,
    )
    reply_markup = None
    if snapshot.plan == "Free":
        invoice_link = await create_plus_invoice_link(message, settings)
        reply_markup = plus_payment_keyboard(invoice_link, settings.plus_stars_price)
    await message.answer(
        format_account_message(
            user_id=snapshot.user_id,
            plan=snapshot.plan,
            used=snapshot.used,
            limit=snapshot.limit,
            plus_daily_limit=settings.plus_daily_limit,
            plus_expires_at=snapshot.plus_expires_at,
            support_url=settings.support_url,
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


@router.message(F.new_chat_members)
async def group_welcome(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if is_private_chat(message):
        return

    bot_user = await message.bot.get_me()
    new_members = message.new_chat_members or []
    if not any(int(member.id) == int(bot_user.id) for member in new_members):
        return

    await app_storage.upsert_group_chat(message.chat)
    if settings.group_welcome_photo:
        try:
            await message.answer_photo(
                photo=resolve_photo_input(settings.group_welcome_photo),
                caption=GROUP_WELCOME_MESSAGE,
                parse_mode="HTML",
            )
            return
        except TelegramBadRequest:
            logger.exception("Group welcome photo could not be sent; falling back to text")

    await message.answer(GROUP_WELCOME_MESSAGE, parse_mode="HTML")


async def create_plus_invoice_link(message: Message, settings: Settings) -> str:
    if message.from_user is None:
        raise RuntimeError("Cannot create a Plus invoice without a Telegram user.")
    return await message.bot.create_invoice_link(
        title=PLUS_TITLE,
        description=PLUS_DESCRIPTION,
        payload=make_plus_payload(message.from_user.id),
        provider_token="",
        currency=PLUS_CURRENCY,
        prices=[LabeledPrice(label=PLUS_PRICE_LABEL, amount=settings.plus_stars_price)],
        subscription_period=settings.plus_period_seconds,
    )


@router.message(F.photo, NonTextChatFilter())
async def photo_chat(
    message: Message,
    settings: Settings,
    app_storage: CatGPTStorage,
    ai_service: CatGPTAI,
) -> None:
    if not await should_handle_non_text(message):
        return
    if not await ensure_user_access(message, settings, app_storage):
        return
    caption = message.caption or ""
    if len(caption) > settings.max_input_chars:
        await answer_user_scoped_message(message, INPUT_TOO_LONG_MESSAGE.format(limit=settings.max_input_chars))
        return

    await answer_user_scoped_message(message, PHOTO_DISABLED_MESSAGE)


@router.message(F.text, TextChatFilter())
async def text_chat(
    message: Message,
    settings: Settings,
    app_storage: CatGPTStorage,
    ai_service: CatGPTAI,
) -> None:
    text = await extract_text_request(message)
    if text is None:
        return
    if text == "":
        if not await ensure_user_access(message, settings, app_storage):
            return
        await message.answer(
            START_MESSAGE,
            reply_to_message_id=message.message_id if not is_private_chat(message) else None,
            allow_sending_without_reply=True,
        )
        return
    if text.startswith("/"):
        return
    if len(text) > settings.max_input_chars:
        await answer_user_scoped_message(message, INPUT_TOO_LONG_MESSAGE.format(limit=settings.max_input_chars))
        return
    if not await ensure_user_access(message, settings, app_storage):
        return
    await handle_ai_chat(message, settings, app_storage, ai_service, UserInput(text=text))


@router.guest_message()
async def guest_chat(
    message: Message,
    settings: Settings,
    app_storage: CatGPTStorage,
    ai_service: CatGPTAI,
) -> None:
    guest_query_id = getattr(message, "guest_query_id", None)
    if not guest_query_id:
        logger.warning("Received a guest message without guest_query_id")
        return

    user = get_guest_caller_user(message)
    if user is None:
        await answer_guest_text(message, guest_query_id, "CatGPT error", GENERIC_AI_ERROR_MESSAGE)
        return

    await app_storage.upsert_user(user)
    await app_storage.upsert_group_chat(message.chat)

    if has_non_text_payload(message):
        await answer_guest_text(message, guest_query_id, "Text only", UNSUPPORTED_ATTACHMENT_MESSAGE)
        return

    text = await extract_guest_text_request(message)
    if text is None:
        await answer_guest_text(message, guest_query_id, "Unsupported message", UNSUPPORTED_ATTACHMENT_MESSAGE)
        return
    if text == "":
        await answer_guest_text(message, guest_query_id, "CatGPT", START_MESSAGE)
        return
    if text.startswith("/"):
        await answer_guest_text(message, guest_query_id, "Unsupported command", UNSUPPORTED_ATTACHMENT_MESSAGE)
        return
    if len(text) > settings.max_input_chars:
        await answer_guest_text(
            message,
            guest_query_id,
            "Message is too long",
            INPUT_TOO_LONG_MESSAGE.format(limit=settings.max_input_chars),
        )
        return

    is_admin = settings.is_admin(user.id) or await app_storage.is_bot_admin(user.id)
    if not is_admin and await app_storage.is_blocked(user.id, message.chat.id):
        await answer_guest_text(message, guest_query_id, "Access blocked", BLOCKED_ACCESS_MESSAGE)
        return

    quota = await app_storage.try_consume_quota(
        telegram_id=user.id,
        chat_id=message.chat.id,
        free_limit=settings.free_daily_limit,
        plus_limit=settings.plus_daily_limit,
        is_admin=is_admin,
    )
    if not quota.allowed:
        await answer_guest_text(
            message,
            guest_query_id,
            "Daily limit reached",
            QUOTA_EXCEEDED_MESSAGE.format(
                limit=quota.limit,
                reset_at=format_timestamp(quota.reset_at),
            ),
        )
        return

    final_text = ""
    try:
        context_messages = await resolve_guest_context_messages(message, user.id)
        async with asyncio.timeout(INLINE_AI_TIMEOUT_SECONDS):
            async for delta in ai_service.stream_chat(
                user.id,
                UserInput(text=text),
                None,
                context_messages=context_messages,
            ):
                final_text += delta
        await answer_guest_text(message, guest_query_id, "CatGPT answer", final_text.strip() or EMPTY_RESPONSE_MESSAGE)
    except TimeoutError:
        await app_storage.refund_usage_event(quota.event_id)
        logger.warning("CatGPT guest AI request exceeded Telegram guest response window")
        await answer_guest_text(message, guest_query_id, "CatGPT is sleeping", AI_TIMEOUT_MESSAGE)
    except AIProviderError as exc:
        await app_storage.refund_usage_event(quota.event_id)
        logger.exception("CatGPT guest AI request failed")
        if exc.is_timeout:
            await answer_guest_text(message, guest_query_id, "Provider timeout", AI_TIMEOUT_MESSAGE)
        else:
            await answer_guest_text(message, guest_query_id, "AI error", GENERIC_AI_ERROR_MESSAGE)
    except Exception:
        await app_storage.refund_usage_event(quota.event_id)
        logger.exception("Unexpected guest chat handling failure")
        await answer_guest_text(message, guest_query_id, "AI error", GENERIC_AI_ERROR_MESSAGE)


@router.inline_query()
async def inline_guest_chat(
    query: InlineQuery,
    settings: Settings,
    app_storage: CatGPTStorage,
    ai_service: CatGPTAI,
) -> None:
    text = query.query.strip()
    if not text:
        await answer_inline_text(query, "CatGPT", START_MESSAGE)
        return
    if len(text) > settings.max_input_chars:
        await answer_inline_text(
            query,
            "Message is too long",
            INPUT_TOO_LONG_MESSAGE.format(limit=settings.max_input_chars),
        )
        return

    await app_storage.upsert_user(query.from_user)
    if await app_storage.is_blocked(query.from_user.id, None):
        await answer_inline_text(query, "Access blocked", BLOCKED_ACCESS_MESSAGE)
        return
    is_admin = settings.is_admin(query.from_user.id) or await app_storage.is_bot_admin(query.from_user.id)
    quota = await app_storage.try_consume_quota(
        telegram_id=query.from_user.id,
        chat_id=0,
        free_limit=settings.free_daily_limit,
        plus_limit=settings.plus_daily_limit,
        is_admin=is_admin,
    )
    if not quota.allowed:
        await answer_inline_text(
            query,
            "Daily limit reached",
            QUOTA_EXCEEDED_MESSAGE.format(
                limit=quota.limit,
                reset_at=format_timestamp(quota.reset_at),
            ),
        )
        return

    final_text = ""
    try:
        async with asyncio.timeout(INLINE_AI_TIMEOUT_SECONDS):
            async for delta in ai_service.stream_chat(query.from_user.id, UserInput(text=text), None):
                final_text += delta
        await answer_inline_text(query, "CatGPT answer", final_text.strip() or EMPTY_RESPONSE_MESSAGE)
    except TimeoutError:
        await app_storage.refund_usage_event(quota.event_id)
        logger.warning("CatGPT inline AI request exceeded Telegram inline response window")
        await answer_inline_text(query, "CatGPT is sleeping", AI_TIMEOUT_MESSAGE)
    except AIProviderError as exc:
        await app_storage.refund_usage_event(quota.event_id)
        logger.exception("CatGPT inline AI request failed")
        if exc.is_timeout:
            await answer_inline_text(query, "Provider timeout", AI_TIMEOUT_MESSAGE)
        else:
            await answer_inline_text(query, "AI error", GENERIC_AI_ERROR_MESSAGE)
    except Exception:
        await app_storage.refund_usage_event(quota.event_id)
        logger.exception("Unexpected inline chat handling failure")
        await answer_inline_text(query, "AI error", GENERIC_AI_ERROR_MESSAGE)


@router.message(NonTextChatFilter())
async def unsupported_attachment(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await should_handle_non_text(message):
        return
    if not await ensure_user_access(message, settings, app_storage):
        return
    await answer_user_scoped_message(message, UNSUPPORTED_ATTACHMENT_MESSAGE)


@router.callback_query(F.data == "force:check")
async def force_join_check(callback: CallbackQuery, app_storage: CatGPTStorage) -> None:
    if callback.from_user is None:
        return
    if callback.message is None:
        await callback.answer(FORCE_JOIN_MISSING_MESSAGE, show_alert=True)
        return
    missing = await missing_required_channels(callback.message.bot, app_storage, callback.from_user.id)
    if missing:
        try:
            await callback.message.edit_text(
                FORCE_JOIN_REQUIRED_MESSAGE,
                reply_markup=force_join_check_keyboard(missing),
            )
        except TelegramBadRequest:
            pass
        await callback.answer(FORCE_JOIN_MISSING_MESSAGE, show_alert=True)
        return
    await callback.answer(FORCE_JOIN_OK_MESSAGE, show_alert=True)
    try:
        await callback.message.edit_text(FORCE_JOIN_OK_MESSAGE, reply_markup=None)
    except TelegramBadRequest:
        logger.warning("Could not update force join message after successful verification")


async def handle_ai_chat(
    message: Message,
    settings: Settings,
    app_storage: CatGPTStorage,
    ai_service: CatGPTAI,
    user_input: UserInput,
) -> None:
    if message.from_user is None:
        return
    await app_storage.upsert_user(message.from_user)
    await app_storage.upsert_group_chat(message.chat)
    is_admin = await is_bot_admin(message, settings, app_storage)
    quota = await app_storage.try_consume_quota(
        telegram_id=message.from_user.id,
        chat_id=message.chat.id,
        free_limit=settings.free_daily_limit,
        plus_limit=settings.plus_daily_limit,
        is_admin=is_admin,
    )
    if not quota.allowed:
        await answer_user_scoped_message(
            message,
            QUOTA_EXCEEDED_MESSAGE.format(
                limit=quota.limit,
                reset_at=format_timestamp(quota.reset_at),
            )
        )
        return

    streamer: TelegramDraftStreamer | None = None
    thinking_message: Message | None = None
    draft_ok = False
    if is_private_chat(message):
        draft_id = (message.message_id * 10_000) + randbelow(10_000) + 1
        streamer = TelegramDraftStreamer(message.bot, message.chat.id, draft_id=draft_id)
        draft_ok = await streamer.start()
    if not draft_ok and is_private_chat(message):
        thinking_message = await message.answer(THINKING_MESSAGE)

    final_text = ""
    try:
        last_turn = await resolve_last_turn(message, app_storage)
        async for delta in ai_service.stream_chat(message.from_user.id, user_input, last_turn):
            final_text += delta
            if draft_ok and streamer is not None:
                await streamer.push(final_text)

        final_text = final_text.strip() or EMPTY_RESPONSE_MESSAGE
        if draft_ok and streamer is not None:
            await streamer.push(final_text, force=True)
        sent_messages = await send_final_text(
            message.bot,
            message.chat.id,
            final_text,
            reply_to_message_id=message.message_id if not is_private_chat(message) else None,
        )
        if thinking_message is not None:
            await delete_message_safely(thinking_message)
        assistant_message_ids = extract_message_ids(sent_messages)
        await app_storage.save_last_turn(
            message.from_user.id,
            user_input.text,
            final_text,
            chat_id=message.chat.id,
            assistant_message_id=assistant_message_ids[0] if assistant_message_ids else None,
            assistant_message_ids=assistant_message_ids,
        )
    except AIProviderError as exc:
        await app_storage.refund_usage_event(quota.event_id)
        logger.exception("CatGPT AI request failed")
        if exc.likely_image_unsupported:
            await message.answer(IMAGE_PROVIDER_ERROR_MESSAGE)
        elif exc.is_timeout:
            await message.answer(AI_TIMEOUT_MESSAGE)
        else:
            await message.answer(GENERIC_AI_ERROR_MESSAGE)
        if thinking_message is not None:
            await delete_message_safely(thinking_message)
    except Exception:
        await app_storage.refund_usage_event(quota.event_id)
        logger.exception("Unexpected chat handling failure")
        await message.answer(GENERIC_AI_ERROR_MESSAGE)
        if thinking_message is not None:
            await delete_message_safely(thinking_message)


async def extract_text_request(message: Message) -> str | None:
    text = message.text or ""
    if is_private_chat(message):
        return text

    bot_user = await message.bot.get_me()
    bot_id = int(bot_user.id)
    bot_username = bot_user.username or ""
    if is_reply_to_bot(message, bot_id):
        return text.strip()

    mentioned_text = strip_bot_mention(text, bot_username)
    return mentioned_text


async def should_handle_text(message: Message) -> bool:
    if is_private_chat(message):
        return True

    bot_user = await message.bot.get_me()
    text = message.text or ""
    if is_command_text(text):
        return False

    if is_reply_to_bot(message, int(bot_user.id)):
        return True

    mentioned_text = strip_bot_mention(text, bot_user.username or "")
    return mentioned_text is not None and not is_command_text(mentioned_text)


async def extract_guest_text_request(message: Message) -> str | None:
    text = (message.text or message.caption or "").strip()
    if not text:
        return ""

    bot_user = await message.bot.get_me()
    bot_username = bot_user.username or ""
    mentioned_text = strip_bot_mention(text, bot_username)
    return mentioned_text if mentioned_text is not None else text


async def should_handle_non_text(message: Message) -> bool:
    if is_private_chat(message):
        return True

    bot_user = await message.bot.get_me()
    if is_reply_to_bot(message, int(bot_user.id)):
        return True

    caption = message.caption or ""
    return strip_bot_mention(caption, bot_user.username or "") is not None


async def resolve_last_turn(
    message: Message,
    app_storage: CatGPTStorage,
) -> tuple[str, str] | None:
    if message.from_user is None:
        return None

    bot_user = await message.bot.get_me()
    if is_reply_to_bot(message, int(bot_user.id)) and message.reply_to_message is not None:
        reply_message_id = message.reply_to_message.message_id
        turn = await app_storage.get_turn_by_assistant_message(message.chat.id, reply_message_id)
        if turn is not None:
            return turn

    return await app_storage.get_last_turn(message.from_user.id, message.chat.id)


async def resolve_guest_context_messages(message: Message, user_id: int) -> list[dict[str, str]] | None:
    reply = message.reply_to_message
    if reply is None:
        return None

    reply_text = extract_message_text(reply)
    if not reply_text:
        return None

    bot_user = await message.bot.get_me()
    if is_message_from_bot(reply, int(bot_user.id)):
        return [
            {
                "role": "user",
                "content": "The user is replying to this previous CatGPT message.",
            },
            {
                "role": "assistant",
                "content": reply_text,
            },
        ]

    if is_message_from_user(reply, user_id):
        return [
            {
                "role": "user",
                "content": f"The user is replying to this previous user message:\n{reply_text}",
            },
        ]

    return None


def is_private_chat(message: Message) -> bool:
    return message.chat.type == "private"


def is_reply_to_bot(message: Message, bot_id: int) -> bool:
    reply = message.reply_to_message
    return bool(reply and reply.from_user and int(reply.from_user.id) == bot_id)


def is_message_from_bot(message: Message, bot_id: int) -> bool:
    sender = message.from_user
    via_bot = message.via_bot
    return bool(sender and int(sender.id) == bot_id) or bool(via_bot and int(via_bot.id) == bot_id)


def is_message_from_user(message: Message, user_id: int) -> bool:
    sender = message.from_user
    return bool(sender and int(sender.id) == int(user_id))


def extract_message_text(message: Message) -> str:
    text = message.text or message.caption or ""
    return text.strip()


def is_command_text(text: str) -> bool:
    return text.lstrip().startswith("/")


def has_non_text_payload(message: Message) -> bool:
    fields = (
        "photo",
        "document",
        "video",
        "animation",
        "audio",
        "voice",
        "video_note",
        "sticker",
        "contact",
        "location",
        "venue",
        "poll",
        "dice",
        "game",
        "story",
        "paid_media",
    )
    return any(bool(getattr(message, field, None)) for field in fields)


def strip_bot_mention(text: str, bot_username: str) -> str | None:
    if not bot_username:
        return None
    mention = f"@{bot_username}".lower()
    lowered = text.lower()
    index = lowered.find(mention)
    if index < 0:
        return None
    return (text[:index] + text[index + len(mention) :]).strip()


def extract_message_ids(messages: list[object]) -> list[int]:
    message_ids: list[int] = []
    for message in messages:
        message_id = getattr(message, "message_id", None)
        if isinstance(message_id, int):
            message_ids.append(message_id)
    return message_ids


async def answer_user_scoped_message(message: Message, text: str) -> Message:
    return await message.answer(
        text,
        reply_to_message_id=message.message_id if not is_private_chat(message) else None,
        allow_sending_without_reply=True,
    )


async def delete_message_safely(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        return


async def answer_inline_text(query: InlineQuery, title: str, text: str) -> None:
    safe_text, parse_mode = format_inline_result_text(text)
    content_kwargs = {
        "message_text": safe_text,
        "disable_web_page_preview": True,
    }
    if parse_mode is not None:
        content_kwargs["parse_mode"] = parse_mode
    await query.answer(
        [
            InlineQueryResultArticle(
                id=uuid4().hex,
                title=title,
                input_message_content=InputTextMessageContent(**content_kwargs),
                description=safe_text[:120],
            )
        ],
        cache_time=0,
        is_personal=True,
    )


async def answer_guest_text(message: Message, guest_query_id: str, title: str, text: str) -> None:
    safe_text, parse_mode = format_inline_result_text(text)
    content_kwargs = {
        "message_text": safe_text,
        "disable_web_page_preview": True,
    }
    if parse_mode is not None:
        content_kwargs["parse_mode"] = parse_mode
    result = InlineQueryResultArticle(
        id=uuid4().hex,
        title=title,
        input_message_content=InputTextMessageContent(**content_kwargs),
        description=safe_text[:120],
    )
    await message.bot.answer_guest_query(guest_query_id=guest_query_id, result=result)


def format_inline_result_text(text: str) -> tuple[str, str | None]:
    if not text:
        return EMPTY_RESPONSE_MESSAGE, None
    chunks = split_final_text(text)
    if not chunks:
        return text[:4096], None
    chunk, parse_mode = chunks[0]
    return chunk[:4096], parse_mode


def get_guest_caller_user(message: Message) -> object | None:
    return message.from_user or getattr(message, "guest_bot_caller_user", None)


def resolve_photo_input(value: str) -> str | FSInputFile:
    path = Path(value).expanduser()
    if path.is_file():
        return FSInputFile(path)
    return value


async def ensure_user_access(message: Message, settings: Settings, app_storage: CatGPTStorage) -> bool:
    if message.from_user is None:
        return False
    await app_storage.upsert_user(message.from_user)
    await app_storage.upsert_group_chat(message.chat)
    if await is_bot_admin(message, settings, app_storage):
        return True
    if await app_storage.is_blocked(message.from_user.id, message.chat.id):
        await message.answer(BLOCKED_ACCESS_MESSAGE)
        return False
    if is_private_chat(message):
        missing = await missing_required_channels(message.bot, app_storage, message.from_user.id)
        if missing:
            await message.answer(FORCE_JOIN_REQUIRED_MESSAGE, reply_markup=force_join_check_keyboard(missing))
            return False
    return True


async def is_bot_admin(message: Message, settings: Settings, app_storage: CatGPTStorage) -> bool:
    user_id = message.from_user.id if message.from_user else None
    return settings.is_admin(user_id) or await app_storage.is_bot_admin(user_id)


async def missing_required_channels(bot, app_storage: CatGPTStorage, user_id: int) -> list[dict[str, object]]:
    if not await app_storage.is_force_sub_enabled():
        return []
    missing: list[dict[str, object]] = []
    for channel in await app_storage.list_required_channels():
        try:
            member = await bot.get_chat_member(chat_id=int(channel["chat_id"]), user_id=user_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            missing.append(channel)
            continue
        status = str(getattr(member, "status", ""))
        if status in {"creator", "administrator", "member"}:
            continue
        if status == "restricted" and bool(getattr(member, "is_member", False)):
            continue
        missing.append(channel)
    return missing
