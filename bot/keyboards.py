from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


ADMIN_BUTTONS = (
    "📊 Stats",
    "🔎 Check User",
    "✍️ Broadcast",
    "👨‍✈️ Admins",
    "🥷 Force Join",
    "⛔️ Block User or Group",
    "📥 Get Database & Logs",
)

ADMIN_BACK = "🔙 Back"
ADMIN_HOME = "🏠 Home"

ADMIN_BROADCAST_USERS = "👤 Users"
ADMIN_BROADCAST_GROUPS = "👥 Groups"
ADMIN_BROADCAST_NORMAL = "📩 Normal"
ADMIN_BROADCAST_FORWARD = "↗️ Forward"

ADMIN_ADMINS_LIST = "📜 Admins list"
ADMIN_ADMINS_ADD = "➕ Add admin"
ADMIN_ADMINS_REMOVE = "➖ Remove admin"

ADMIN_FORCE_STATUS = "📊 Status"
ADMIN_FORCE_ADD = "💠 Add channel"
ADMIN_FORCE_REMOVE = "♨️ Remove channel"
ADMIN_FORCE_DISABLE = "⛔ Disable"
ADMIN_FORCE_ENABLE = "✅ Enable"

ADMIN_BLOCKS_LIST = "📜 Blocked list"
ADMIN_BLOCKS_ADD = "➕ Add block"
ADMIN_BLOCKS_REMOVE = "➖ Remove block"
ADMIN_BOT_PAUSE = "⏸ Pause Bot"
ADMIN_BOT_RESUME = "▶️ Resume Bot"

ADMIN_GRANT_PLUS = "⭐ Grant Plus"
ADMIN_REVOKE_PLUS = "❌ Revoke Plus"
ADMIN_BLOCK_USER = "⛔️ Block User"
ADMIN_UNBLOCK_USER = "✅ Unblock User"


def admin_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_BUTTONS[1])
    kb.button(text=ADMIN_BUTTONS[0])
    kb.button(text=ADMIN_BUTTONS[2])
    kb.button(text=ADMIN_BUTTONS[4])
    kb.button(text=ADMIN_BUTTONS[3])
    kb.button(text=ADMIN_BUTTONS[5])
    kb.button(text=ADMIN_BUTTONS[6])
    kb.adjust(2, 3, 2)
    return kb.as_markup(resize_keyboard=True, input_field_placeholder="Choose an admin action")


def admin_back_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_BACK)
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)


def admin_home_back_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_HOME)
    kb.button(text=ADMIN_BACK)
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


def admin_broadcast_target_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_BROADCAST_USERS)
    kb.button(text=ADMIN_BROADCAST_GROUPS)
    kb.button(text=ADMIN_BACK)
    kb.adjust(2, 1)
    return kb.as_markup(resize_keyboard=True)


def admin_broadcast_mode_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_BROADCAST_NORMAL)
    kb.button(text=ADMIN_BROADCAST_FORWARD)
    kb.button(text=ADMIN_HOME)
    kb.button(text=ADMIN_BACK)
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)


def admin_admins_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_ADMINS_LIST)
    kb.button(text=ADMIN_ADMINS_ADD)
    kb.button(text=ADMIN_ADMINS_REMOVE)
    kb.button(text=ADMIN_BACK)
    kb.adjust(1, 2, 1)
    return kb.as_markup(resize_keyboard=True)


def admin_force_join_keyboard(enabled: bool) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_FORCE_STATUS)
    kb.button(text=ADMIN_FORCE_ADD)
    kb.button(text=ADMIN_FORCE_REMOVE)
    kb.button(text=ADMIN_FORCE_DISABLE if enabled else ADMIN_FORCE_ENABLE)
    kb.button(text=ADMIN_BACK)
    kb.adjust(1, 2, 1, 1)
    return kb.as_markup(resize_keyboard=True)


def admin_blocks_keyboard(is_paused: bool = False) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_BLOCKS_LIST)
    kb.button(text=ADMIN_BLOCKS_ADD)
    kb.button(text=ADMIN_BLOCKS_REMOVE)
    kb.button(text=ADMIN_BOT_RESUME if is_paused else ADMIN_BOT_PAUSE)
    kb.button(text=ADMIN_BACK)
    kb.adjust(1, 2, 1, 1)
    return kb.as_markup(resize_keyboard=True)


def admin_id_select_keyboard(ids: list[int], include_home: bool = False) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    for value in ids:
        kb.button(text=str(int(value)))
    if include_home:
        kb.button(text=ADMIN_HOME)
    kb.button(text=ADMIN_BACK)

    row_sizes = [2] * (len(ids) // 2)
    if len(ids) % 2:
        row_sizes.append(1)
    row_sizes.append(2 if include_home else 1)
    kb.adjust(*row_sizes)
    return kb.as_markup(resize_keyboard=True)


def admin_user_action_keyboard(*, is_plus: bool, is_blocked: bool) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=ADMIN_REVOKE_PLUS if is_plus else ADMIN_GRANT_PLUS)
    kb.button(text=ADMIN_UNBLOCK_USER if is_blocked else ADMIN_BLOCK_USER)
    kb.button(text=ADMIN_HOME)
    kb.button(text=ADMIN_BACK)
    kb.adjust(1, 1, 2)
    return kb.as_markup(resize_keyboard=True)


def force_join_check_keyboard(channels: list[dict[str, object]]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for channel in channels:
        username = str(channel.get("username") or "").strip().lstrip("@")
        title = str(channel.get("title") or username or "Channel")
        if not username:
            continue
        buttons.append([InlineKeyboardButton(text=title, url=f"https://t.me/{username}")])
    buttons.append([InlineKeyboardButton(text="✅ Check membership", callback_data="force:check")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def plus_payment_keyboard(invoice_link: str, price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ Plus - {price} Stars",
                    url=invoice_link,
                )
            ]
        ]
    )


def admin_plus_users_inline_keyboard(count: int = 0) -> InlineKeyboardMarkup:
    button_text = f"⭐ Plus Users ({count})" if count > 0 else "⭐ Plus Users"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data="admin:plus_users",
                )
            ]
        ]
    )
