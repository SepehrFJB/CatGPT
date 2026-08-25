from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Filter
from aiogram.types import Message, ReplyKeyboardRemove

from bot.config import Settings
from bot.database import CatGPTStorage, format_timestamp
from bot.keyboards import (
    ADMIN_ADMINS_ADD,
    ADMIN_ADMINS_LIST,
    ADMIN_ADMINS_REMOVE,
    ADMIN_BACK,
    ADMIN_BLOCKS_ADD,
    ADMIN_BLOCKS_LIST,
    ADMIN_BLOCKS_REMOVE,
    ADMIN_BLOCK_USER,
    ADMIN_BROADCAST_FORWARD,
    ADMIN_BROADCAST_GROUPS,
    ADMIN_BROADCAST_NORMAL,
    ADMIN_BROADCAST_USERS,
    ADMIN_BUTTONS,
    ADMIN_FORCE_ADD,
    ADMIN_FORCE_DISABLE,
    ADMIN_FORCE_ENABLE,
    ADMIN_FORCE_REMOVE,
    ADMIN_FORCE_STATUS,
    ADMIN_GRANT_PLUS,
    ADMIN_HOME,
    ADMIN_REVOKE_PLUS,
    ADMIN_UNBLOCK_USER,
    admin_admins_keyboard,
    admin_back_keyboard,
    admin_blocks_keyboard,
    admin_broadcast_mode_keyboard,
    admin_broadcast_target_keyboard,
    admin_force_join_keyboard,
    admin_home_back_keyboard,
    admin_id_select_keyboard,
    admin_keyboard,
    admin_user_action_keyboard,
)
from bot.texts import (
    ADMIN_PANEL_MESSAGE,
    BLOCKED_BY_ADMIN_MESSAGE,
    GROUP_BLOCKED_BY_ADMIN_MESSAGE,
    GROUP_UNBLOCKED_BY_ADMIN_MESSAGE,
    PLUS_ADMIN_GRANTED_MESSAGE,
    PLUS_ADMIN_REVOKED_MESSAGE,
    UNBLOCKED_BY_ADMIN_MESSAGE,
)
from bot.services.backup import DatabaseBackupService

logger = logging.getLogger(__name__)
router = Router(name="admin")

STATE_INSPECT_USER = "inspect_user"
STATE_BROADCAST_TARGET = "broadcast_target"
STATE_BROADCAST_MODE = "broadcast_mode"
STATE_BROADCAST_NORMAL = "broadcast_normal"
STATE_BROADCAST_FORWARD = "broadcast_forward"
STATE_ADMIN_ADD = "admin_add"
STATE_ADMIN_REMOVE = "admin_remove"
STATE_FORCE_ADD = "force_add"
STATE_FORCE_REMOVE = "force_remove"
STATE_BLOCK_ADD = "block_add"
STATE_BLOCK_REMOVE = "block_remove"
STATE_INSPECT_RESULT = "inspect_result"

AdminStateKey = tuple[int, int]
admin_states: dict[AdminStateKey, dict[str, Any]] = {}

ADMIN_KEYBOARD_TEXTS = frozenset(
    {
        *ADMIN_BUTTONS,
        ADMIN_HOME,
        ADMIN_BACK,
        ADMIN_BROADCAST_USERS,
        ADMIN_BROADCAST_GROUPS,
        ADMIN_BROADCAST_NORMAL,
        ADMIN_BROADCAST_FORWARD,
        ADMIN_ADMINS_LIST,
        ADMIN_ADMINS_ADD,
        ADMIN_ADMINS_REMOVE,
        ADMIN_FORCE_STATUS,
        ADMIN_FORCE_ADD,
        ADMIN_FORCE_REMOVE,
        ADMIN_FORCE_DISABLE,
        ADMIN_FORCE_ENABLE,
        ADMIN_BLOCKS_LIST,
        ADMIN_BLOCKS_ADD,
        ADMIN_BLOCKS_REMOVE,
        ADMIN_GRANT_PLUS,
        ADMIN_REVOKE_PLUS,
        ADMIN_BLOCK_USER,
        ADMIN_UNBLOCK_USER,
    }
)


class AdminStateFilter(Filter):
    async def __call__(self, message: Message, app_storage: CatGPTStorage) -> bool:
        if message.from_user is None:
            return False
        if not is_private_chat(message):
            return False
        if admin_state_key(message) not in admin_states:
            return False
        return await app_storage.is_bot_admin(message.from_user.id)


@router.message(F.chat.type != "private", F.text.in_(ADMIN_KEYBOARD_TEXTS))
async def admin_keyboard_outside_private(message: Message) -> None:
    clear_admin_state(message)
    await message.answer(
        "Admin panel is available in private chat only.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(F.text == ADMIN_HOME)
@router.message(F.text == ADMIN_BACK)
async def admin_back_or_home(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    clear_admin_state(message)
    await message.answer(ADMIN_PANEL_MESSAGE, reply_markup=admin_keyboard())


@router.message(F.text == ADMIN_BUTTONS[0])
async def admin_stats(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    stats = await app_storage.admin_stats(settings.free_daily_limit, settings.plus_daily_limit)
    await message.answer(
        "\n".join(
            [
                "📊 BOT STATISTICS",
                "",
                "👥 Users",
                f"├ Total Users: {stats['users']}",
                f"├ Groups: {stats['groups']}",
                f"└ Plus Users: {stats['plus_users']}",
                "",
                "🤖 AI Activity",
                f"├ Requests Today: {stats['requests_today']}",
                "└ Daily Reset: 00:00 UTC",
                "",
                "💰 Revenue",
                f"└ Payments: {stats['payments']}",
                "",
                "🛡 Moderation",
                f"├ Blocked Users/Groups: {stats['blocked']}",
                f"├ Force Join: {stats['force_sub']}",
                f"└ Required Channels: {stats['channels']}",
                "",
                "📈 Limits",
                f"├ Free Plan: {stats['free_limit']} msgs/day",
                f"└ Plus Plan: {stats['plus_limit']} msgs/day",
            ]
        ),
        reply_markup=admin_keyboard(),
    )


@router.message(F.text == ADMIN_BUTTONS[1])
async def admin_check_user(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    set_admin_state(message, STATE_INSPECT_USER)
    await message.answer("🔎 Enter target numeric user ID:", reply_markup=admin_back_keyboard())


@router.message(F.text == ADMIN_BUTTONS[2])
async def admin_broadcast(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    set_admin_state(message, STATE_BROADCAST_TARGET)
    await message.answer("📢 Choose broadcast target:", reply_markup=admin_broadcast_target_keyboard())


@router.message(F.text == ADMIN_BUTTONS[3])
async def admin_admins(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    clear_admin_state(message)
    await message.answer(
        "👨‍✈️ Admin management\n\nUse the menu below to list, add, or remove bot admins.",
        reply_markup=admin_admins_keyboard(),
    )


@router.message(F.text == ADMIN_BUTTONS[4])
async def admin_force_join(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    clear_admin_state(message)
    enabled = await app_storage.is_force_sub_enabled()
    await message.answer(
        "🥷 Force Join management\n\nManage required channels and membership checks.",
        reply_markup=admin_force_join_keyboard(enabled),
    )


@router.message(F.text == ADMIN_BUTTONS[5])
async def admin_blocks(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_owner(message, app_storage):
        await message.answer("⛔️ Only the owner can block a user or group.", reply_markup=admin_keyboard())
        return
    clear_admin_state(message)
    await message.answer(
        "⛔️ Block management\n\nView blocked targets, add a new block, or remove a block.",
        reply_markup=admin_blocks_keyboard(),
    )


@router.message(F.text == ADMIN_BUTTONS[6])
async def admin_get_database(
    message: Message,
    app_storage: CatGPTStorage,
    backup_service: DatabaseBackupService,
) -> None:
    if not await is_owner(message, app_storage):
        await message.answer("⛔️ Only the owner can get the database and logs.", reply_markup=admin_keyboard())
        return
    preparing_message = await message.answer("⏳ Preparing backup and log files...")
    try:
        await backup_service.send_backup_to_chat(
            message.chat.id,
            manual=True,
            reply_markup=admin_keyboard(),
        )
        try:
            await preparing_message.delete()
        except TelegramBadRequest:
            pass
    except Exception:
        logger.exception("Manual database and log backup failed")
        await message.answer("⛔️ Could not create or send the backup and log files.", reply_markup=admin_keyboard())


@router.message(F.text.in_({ADMIN_BROADCAST_USERS, ADMIN_BROADCAST_GROUPS}))
async def admin_broadcast_target(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    state = get_admin_state(message)
    if state.get("state") != STATE_BROADCAST_TARGET:
        return
    target = "groups" if message.text == ADMIN_BROADCAST_GROUPS else "users"
    set_admin_state(message, STATE_BROADCAST_MODE, target=target)
    await message.answer("📨 Choose broadcast mode:", reply_markup=admin_broadcast_mode_keyboard())


@router.message(F.text.in_({ADMIN_BROADCAST_NORMAL, ADMIN_BROADCAST_FORWARD}))
async def admin_broadcast_mode(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    state = get_admin_state(message)
    if state.get("state") != STATE_BROADCAST_MODE:
        return
    mode = "forward" if message.text == ADMIN_BROADCAST_FORWARD else "normal"
    set_admin_state(
        message,
        STATE_BROADCAST_FORWARD if mode == "forward" else STATE_BROADCAST_NORMAL,
        target=state.get("target", "users"),
    )
    prompt = "↗️ Send or forward the message I should copy." if mode == "forward" else "📩 Send broadcast text:"
    await message.answer(prompt, reply_markup=admin_home_back_keyboard())


@router.message(F.text == ADMIN_ADMINS_LIST)
async def admin_admins_list(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    admins = await app_storage.list_bot_admins()
    lines = ["✅ Current bot admins", ""]
    if not admins:
        lines.append("No admins found.")
    else:
        for admin in admins:
            role = str(admin.get("role") or "admin").title()
            lines.append(f"• {role} - {admin.get('user_id')}")
    await message.answer("\n".join(lines), reply_markup=admin_admins_keyboard())


@router.message(F.text == ADMIN_ADMINS_ADD)
async def admin_admins_add(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_owner(message, app_storage):
        await message.answer("⛔️ Only the owner can add a new admin.", reply_markup=admin_admins_keyboard())
        return
    set_admin_state(message, STATE_ADMIN_ADD)
    await message.answer("🔢 Enter the user ID:", reply_markup=admin_home_back_keyboard())


@router.message(F.text == ADMIN_ADMINS_REMOVE)
async def admin_admins_remove(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_owner(message, app_storage):
        await message.answer("⛔️ Only the owner can remove an admin.", reply_markup=admin_admins_keyboard())
        return
    admins = await app_storage.list_bot_admins()
    removable = [int(item["user_id"]) for item in admins if str(item.get("role")) != "owner"]
    if not removable:
        await message.answer("ℹ️ There are no removable admins.", reply_markup=admin_admins_keyboard())
        return
    set_admin_state(message, STATE_ADMIN_REMOVE)
    await message.answer("⚠️ Select an admin to remove:", reply_markup=admin_id_select_keyboard(removable, True))


@router.message(F.text == ADMIN_FORCE_STATUS)
async def admin_force_status(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    await send_force_join_status(message, app_storage)


@router.message(F.text == ADMIN_FORCE_ADD)
async def admin_force_add(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    set_admin_state(message, STATE_FORCE_ADD)
    await message.answer("💠 Send public channel username or link:", reply_markup=admin_home_back_keyboard())


@router.message(F.text == ADMIN_FORCE_REMOVE)
async def admin_force_remove(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    channels = await app_storage.list_required_channels()
    if not channels:
        enabled = await app_storage.is_force_sub_enabled()
        await message.answer("ℹ️ No required channels are configured.", reply_markup=admin_force_join_keyboard(enabled))
        return
    ids = [int(channel["chat_id"]) for channel in channels]
    set_admin_state(message, STATE_FORCE_REMOVE)
    await message.answer("♨️ Select channel ID to remove:", reply_markup=admin_id_select_keyboard(ids, True))


@router.message(F.text.in_({ADMIN_FORCE_DISABLE, ADMIN_FORCE_ENABLE}))
async def admin_force_toggle(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    enabled = message.text == ADMIN_FORCE_ENABLE
    await app_storage.set_force_sub_enabled(enabled)
    await message.answer(
        f"✅ Force Join is now {'enabled' if enabled else 'disabled'}.",
        reply_markup=admin_force_join_keyboard(enabled),
    )


@router.message(F.text == ADMIN_BLOCKS_LIST)
async def admin_blocks_list(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_owner(message, app_storage):
        await message.answer("⛔️ Only the owner can view blocked targets.", reply_markup=admin_keyboard())
        return
    blocked = await app_storage.list_blocked_targets()
    lines = ["✅ Blocked targets", ""]
    if not blocked:
        lines.append("No blocked targets found.")
    else:
        for item in blocked:
            label = "Group" if str(item["target_type"]) == "group" else "User"
            lines.append(f"• {label} - {item['target_id']}")
    await message.answer("\n".join(lines), reply_markup=admin_blocks_keyboard())


@router.message(F.text == ADMIN_BLOCKS_ADD)
async def admin_blocks_add(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_owner(message, app_storage):
        await message.answer("⛔️ Only the owner can block a user or group.", reply_markup=admin_keyboard())
        return
    set_admin_state(message, STATE_BLOCK_ADD)
    await message.answer(
        "🔒 Enter target user/group ID:\n"
        "• User: positive integer, like 123456789\n"
        "• Group: negative chat_id, like -1001234567890",
        reply_markup=admin_home_back_keyboard(),
    )


@router.message(F.text == ADMIN_BLOCKS_REMOVE)
async def admin_blocks_remove(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_owner(message, app_storage):
        await message.answer("⛔️ Only the owner can remove blocks.", reply_markup=admin_keyboard())
        return
    blocked = await app_storage.list_blocked_targets()
    ids = [int(item["target_id"]) for item in blocked]
    if not ids:
        await message.answer("ℹ️ Blocked list is empty.", reply_markup=admin_blocks_keyboard())
        return
    set_admin_state(message, STATE_BLOCK_REMOVE)
    await message.answer("⚠️ Select target ID to remove block:", reply_markup=admin_id_select_keyboard(ids, True))


@router.message(F.text.in_({ADMIN_GRANT_PLUS, ADMIN_REVOKE_PLUS, ADMIN_BLOCK_USER, ADMIN_UNBLOCK_USER}))
async def admin_user_quick_action(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if not await is_admin(message, settings, app_storage):
        return
    state = get_admin_state(message)
    if state.get("state") != STATE_INSPECT_RESULT:
        await message.answer("🔎 Check a user first.", reply_markup=admin_keyboard())
        return
    target_user_id = parse_int(str(state.get("target_user_id") or ""))
    if target_user_id is None or target_user_id <= 0:
        clear_admin_state(message)
        await message.answer("⛔️ Target user is missing. Please check the user again.", reply_markup=admin_keyboard())
        return

    if message.text == ADMIN_GRANT_PLUS:
        expires_at = await app_storage.activate_plus(target_user_id, settings.plus_period_seconds)
        await notify_target_safely(
            message,
            target_user_id,
            PLUS_ADMIN_GRANTED_MESSAGE.format(expires_at=format_timestamp(expires_at)),
        )
        await message.answer(
            f"✅ Plus granted to {target_user_id} until {format_timestamp(expires_at)}.",
        )
        await send_inspected_user_profile(message, settings, app_storage, target_user_id)
        return

    if message.text == ADMIN_REVOKE_PLUS:
        await app_storage.revoke_plus(target_user_id)
        await notify_target_safely(message, target_user_id, PLUS_ADMIN_REVOKED_MESSAGE)
        await message.answer(f"✅ Plus revoked for {target_user_id}.")
        await send_inspected_user_profile(message, settings, app_storage, target_user_id)
        return

    if message.text == ADMIN_BLOCK_USER:
        if await app_storage.is_bot_admin(target_user_id):
            await message.answer("⛔️ Bot admins cannot be blocked.")
            await send_inspected_user_profile(message, settings, app_storage, target_user_id)
            return
        await app_storage.add_blocked_target(target_user_id)
        await notify_target_safely(message, target_user_id, BLOCKED_BY_ADMIN_MESSAGE)
        await message.answer(f"✅ User {target_user_id} was blocked successfully.")
        await send_inspected_user_profile(message, settings, app_storage, target_user_id)
        return

    if message.text == ADMIN_UNBLOCK_USER:
        removed = await app_storage.remove_blocked_target(target_user_id)
        if removed:
            await notify_target_safely(message, target_user_id, UNBLOCKED_BY_ADMIN_MESSAGE)
        text = f"✅ User {target_user_id} was unblocked." if removed else "ℹ️ User was not blocked."
        await message.answer(text)
        await send_inspected_user_profile(message, settings, app_storage, target_user_id)


@router.message(AdminStateFilter())
async def admin_state_input(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    if message.from_user is None:
        return
    if not await app_storage.is_bot_admin(message.from_user.id):
        return

    state = get_admin_state(message)
    state_name = state.get("state")
    if not state_name:
        return

    if state_name == STATE_INSPECT_USER:
        await handle_inspect_user(message, settings, app_storage)
    elif state_name == STATE_BROADCAST_NORMAL:
        await handle_broadcast_payload(message, app_storage, mode="normal")
    elif state_name == STATE_BROADCAST_FORWARD:
        await handle_broadcast_payload(message, app_storage, mode="forward")
    elif state_name == STATE_ADMIN_ADD:
        await handle_admin_add(message, app_storage)
    elif state_name == STATE_ADMIN_REMOVE:
        await handle_admin_remove(message, app_storage)
    elif state_name == STATE_FORCE_ADD:
        await handle_force_add(message, app_storage)
    elif state_name == STATE_FORCE_REMOVE:
        await handle_force_remove(message, app_storage)
    elif state_name == STATE_BLOCK_ADD:
        await handle_block_add(message, app_storage)
    elif state_name == STATE_BLOCK_REMOVE:
        await handle_block_remove(message, app_storage)


async def handle_inspect_user(message: Message, settings: Settings, app_storage: CatGPTStorage) -> None:
    user_id = parse_int(message.text)
    if user_id is None or user_id <= 0:
        await message.answer("⛔️ Invalid input. Only numeric user ID is allowed.", reply_markup=admin_back_keyboard())
        return
    await send_inspected_user_profile(message, settings, app_storage, user_id)


async def send_inspected_user_profile(
    message: Message,
    settings: Settings,
    app_storage: CatGPTStorage,
    user_id: int,
) -> None:
    profile = await app_storage.get_user_admin_profile(user_id, settings.free_daily_limit, settings.plus_daily_limit)
    if profile is None:
        clear_admin_state(message)
        await message.answer("User was not found in the bot database.", reply_markup=admin_keyboard())
        return
    set_admin_state(message, STATE_INSPECT_RESULT, target_user_id=user_id)
    username = f"@{profile['username']}" if profile.get("username") else "Not set"
    full_name = " ".join(str(profile.get(key) or "") for key in ("first_name", "last_name")).strip() or "Not set"
    plus_expires = format_timestamp(profile.get("plus_expires_at")) if profile.get("plus_expires_at") else "Not Active"
    last_activity = format_timestamp(profile.get("last_turn_at")) if profile.get("last_turn_at") else "No Activity"
    is_plus = str(profile["plan"]) == "Plus"
    is_blocked = bool(profile["is_blocked"])
    await message.answer(
        "\n".join(
            [
                "🔎 USER PROFILE",
                "",
                "👤 Identity",
                f"├ ID: {profile['telegram_id']}",
                f"├ Username: {username}",
                f"└ Name: {full_name}",
                "",
                "💎 Subscription",
                f"├ Plan: {profile['plan']}",
                f"├ Daily Usage: {profile['daily_used']} / {profile['daily_limit']}",
                f"└ Plus Expires: {plus_expires}",
                "",
                "💰 Payments",
                f"└ Total Payments: {profile['payment_count']}",
                "",
                "🛡 Status",
                f"├ Blocked: {'Yes' if is_blocked else 'No'}",
                f"└ Last Activity: {last_activity}",
            ]
        ),
        reply_markup=admin_user_action_keyboard(is_plus=is_plus, is_blocked=is_blocked),
    )


async def handle_broadcast_payload(message: Message, app_storage: CatGPTStorage, mode: str) -> None:
    state = get_admin_state(message)
    target = str(state.get("target") or "users")
    if mode == "normal" and not (message.text or "").strip():
        await message.answer("⛔️ Send a text message for normal broadcast.", reply_markup=admin_home_back_keyboard())
        return

    clear_admin_state(message)
    target_chat_ids = await app_storage.list_group_chat_ids() if target == "groups" else await app_storage.list_user_ids()
    if not target_chat_ids:
        await message.answer(f"⛔️ No {target} found for broadcast.", reply_markup=admin_keyboard())
        return

    await message.answer(f"📢 Broadcast started...\nTarget: {target}\nTotal: {len(target_chat_ids)}", reply_markup=admin_keyboard())
    sent = 0
    failed = 0
    blocked = 0
    for target_chat_id in target_chat_ids:
        ok, status = await send_broadcast_to_chat(message, int(target_chat_id), mode)
        if ok:
            sent += 1
        elif status == "blocked":
            blocked += 1
        else:
            failed += 1
        await asyncio.sleep(0.04)

    await message.answer(
        "\n".join(
            [
                "✅ Broadcast finished.",
                f"Sent: {sent}",
                f"Blocked/Inactive: {blocked}",
                f"Failed: {failed}",
            ]
        )
    )


async def handle_admin_add(message: Message, app_storage: CatGPTStorage) -> None:
    user_id = parse_int(message.text)
    if user_id is None or user_id <= 0:
        await message.answer("⛔️ Invalid input. Only a valid user ID is allowed.", reply_markup=admin_home_back_keyboard())
        return
    await app_storage.add_bot_admin(user_id)
    clear_admin_state(message)
    await message.answer(f"✅ User {user_id} is now a bot admin.", reply_markup=admin_admins_keyboard())


async def handle_admin_remove(message: Message, app_storage: CatGPTStorage) -> None:
    user_id = parse_int(message.text)
    if user_id is None:
        await message.answer("⛔️ Select or send a valid admin ID.", reply_markup=admin_home_back_keyboard())
        return
    removed = await app_storage.remove_bot_admin(user_id)
    clear_admin_state(message)
    text = f"✅ User {user_id} was removed from bot admins." if removed else "⛔️ This admin cannot be removed."
    await message.answer(text, reply_markup=admin_admins_keyboard())


async def handle_force_add(message: Message, app_storage: CatGPTStorage) -> None:
    channel_ref = parse_channel_reference(message.text or "")
    if channel_ref is None:
        await message.answer("⛔️ Send a valid public channel username or link.", reply_markup=admin_home_back_keyboard())
        return
    enabled = await app_storage.is_force_sub_enabled()
    try:
        chat = await message.bot.get_chat(channel_ref)
        me = await message.bot.get_me()
        await message.bot.get_chat_member(chat_id=chat.id, user_id=me.id)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(
            "⛔️ I cannot read this channel. Add the bot as admin and try again.",
            reply_markup=admin_force_join_keyboard(enabled),
        )
        clear_admin_state(message)
        return

    if str(chat.type) not in {"channel", "supergroup"} or not getattr(chat, "username", None):
        await message.answer(
            "⛔️ Only public channels or supergroups with username can be added.",
            reply_markup=admin_force_join_keyboard(enabled),
        )
        clear_admin_state(message)
        return

    await app_storage.add_required_channel(chat.id, chat.username, chat.title)
    clear_admin_state(message)
    await message.answer(
        f"✅ @{chat.username} was added to Force Join.",
        reply_markup=admin_force_join_keyboard(enabled),
    )


async def handle_force_remove(message: Message, app_storage: CatGPTStorage) -> None:
    channel_id = parse_int(message.text)
    if channel_id is None:
        await message.answer("⛔️ Select or send a valid channel ID.", reply_markup=admin_home_back_keyboard())
        return
    removed = await app_storage.remove_required_channel(channel_id)
    enabled = await app_storage.is_force_sub_enabled()
    clear_admin_state(message)
    text = f"✅ Channel {channel_id} was removed." if removed else "⛔️ Channel not found."
    await message.answer(text, reply_markup=admin_force_join_keyboard(enabled))


async def handle_block_add(message: Message, app_storage: CatGPTStorage) -> None:
    target_id = parse_int(message.text)
    if target_id is None or target_id == 0:
        await message.answer("⛔️ Send a valid user/group ID.", reply_markup=admin_home_back_keyboard())
        return
    if await app_storage.is_bot_admin(target_id):
        await message.answer("⛔️ Bot admins cannot be blocked.", reply_markup=admin_blocks_keyboard())
        clear_admin_state(message)
        return
    target_type = await app_storage.add_blocked_target(target_id)
    clear_admin_state(message)
    label = "Group" if target_type == "group" else "User"
    await notify_target_safely(
        message,
        target_id,
        GROUP_BLOCKED_BY_ADMIN_MESSAGE if target_type == "group" else BLOCKED_BY_ADMIN_MESSAGE,
    )
    await message.answer(f"✅ {label} {target_id} was blocked successfully.", reply_markup=admin_blocks_keyboard())


async def handle_block_remove(message: Message, app_storage: CatGPTStorage) -> None:
    target_id = parse_int(message.text)
    if target_id is None:
        await message.answer("⛔️ Select or send a valid blocked ID.", reply_markup=admin_home_back_keyboard())
        return
    removed = await app_storage.remove_blocked_target(target_id)
    clear_admin_state(message)
    if removed:
        await notify_target_safely(
            message,
            target_id,
            GROUP_UNBLOCKED_BY_ADMIN_MESSAGE if target_id < 0 else UNBLOCKED_BY_ADMIN_MESSAGE,
        )
    text = f"✅ Block removed for ID {target_id}." if removed else "⛔️ Block was not found."
    await message.answer(text, reply_markup=admin_blocks_keyboard())


async def send_force_join_status(message: Message, app_storage: CatGPTStorage) -> None:
    enabled = await app_storage.is_force_sub_enabled()
    channels = await app_storage.list_required_channels()
    lines = [
        "✅ Current Force Join status",
        "",
        f"Force Join: {'Enabled' if enabled else 'Disabled'}",
        "",
        "Channels:",
    ]
    if not channels:
        lines.append("• No channels configured.")
    else:
        for channel in channels:
            username = f"@{channel['username']}" if channel.get("username") else "No username"
            lines.append(f"• {username} - {channel['chat_id']}")
    await message.answer("\n".join(lines), reply_markup=admin_force_join_keyboard(enabled))


async def send_broadcast_to_chat(message: Message, target_chat_id: int, mode: str) -> tuple[bool, str]:
    for attempt in range(2):
        try:
            if mode == "forward":
                await message.bot.copy_message(
                    chat_id=target_chat_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
            else:
                await message.bot.send_message(target_chat_id, message.text or "")
            return True, "sent"
        except TelegramRetryAfter as exc:
            if attempt == 0:
                await asyncio.sleep(float(exc.retry_after) + 0.2)
                continue
            return False, "failed"
        except TelegramForbiddenError:
            return False, "blocked"
        except TelegramBadRequest:
            return False, "failed"
        except Exception:
            logger.exception("Broadcast failed for chat_id=%s", target_chat_id)
            return False, "failed"
    return False, "failed"


async def is_admin(message: Message, settings: Settings, app_storage: CatGPTStorage) -> bool:
    if not is_private_chat(message):
        return False
    user_id = message.from_user.id if message.from_user else None
    return settings.is_admin(user_id) or await app_storage.is_bot_admin(user_id)


async def is_owner(message: Message, app_storage: CatGPTStorage) -> bool:
    if not is_private_chat(message):
        return False
    user_id = message.from_user.id if message.from_user else None
    return await app_storage.get_bot_admin_role(user_id) == "owner"


def is_private_chat(message: Message) -> bool:
    return message.chat.type == "private"


def get_admin_state(message: Message) -> dict[str, Any]:
    return admin_states.get(admin_state_key(message), {})


def set_admin_state(message: Message, state: str, **data: Any) -> None:
    admin_states[admin_state_key(message)] = {**data, "state": state}


def clear_admin_state(message: Message) -> None:
    if message.from_user is not None:
        admin_states.pop(admin_state_key(message), None)


def admin_state_key(message: Message) -> AdminStateKey:
    user_id = int(message.from_user.id) if message.from_user else 0
    return user_id, int(message.chat.id)


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if not re.fullmatch(r"-?\d+", value):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_channel_reference(raw: str) -> str | int | None:
    value = raw.strip()
    if not value:
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    if value.startswith("@") and is_valid_telegram_username(value[1:]):
        return value
    if is_valid_telegram_username(value):
        return f"@{value}"
    if value.startswith(("http://", "https://")):
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        host = parsed.netloc.lower().removeprefix("www.")
        if host not in {"t.me", "telegram.me"}:
            return None
        username = parsed.path.strip("/").split("/")[0]
        if is_valid_telegram_username(username):
            return f"@{username}"
    return None


def is_valid_telegram_username(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{5,}", value))


async def notify_target_safely(message: Message, target_id: int, text: str) -> None:
    try:
        await message.bot.send_message(chat_id=target_id, text=text)
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning("Could not notify target %s", target_id)
