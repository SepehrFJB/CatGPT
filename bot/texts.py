from __future__ import annotations

from datetime import UTC, datetime
from html import escape

START_MESSAGE = (
    "Hi, I am CatGPT. I am a friendly cat-shaped AI assistant.\n\n"
    "Send me a message, and I will help.\n\n"
    "@TheCatGPTAI"
)
GROUP_WELCOME_MESSAGE = (
    "🐾 <b>Hi, I am CatGPT.</b>\n\n"
    "I am here to help this group with questions, writing, coding, ideas, summaries, and quick explanations.\n\n"
    "You can talk to me by mentioning me or replying to one of my messages. "
    "I will keep the conversation light, useful, and friendly.\n\n"
    "Let’s make this group a little smarter together."
)

ADMIN_PANEL_MESSAGE = "Welcome to the admin panel 👋"
ADMIN_PLACEHOLDER_MESSAGE = "This admin action is not configured yet."

INPUT_TOO_LONG_MESSAGE = "Please keep your message under {limit} characters."
UNSUPPORTED_ATTACHMENT_MESSAGE = "I can only accept text and photo messages right now."
PHOTO_DISABLED_MESSAGE = "Photo analysis is currently disabled. Please send text instead."
BLOCKED_ACCESS_MESSAGE = "⛔️ Your access to this bot is blocked."
FORCE_JOIN_REQUIRED_MESSAGE = "To use CatGPT, please join the required channels first:"
FORCE_JOIN_OK_MESSAGE = "✅ Membership verified. You can use CatGPT now."
FORCE_JOIN_MISSING_MESSAGE = "You have not joined all required channels yet."
QUOTA_EXCEEDED_MESSAGE = (
    "You have reached your daily limit of {limit} messages. "
    "Your quota refreshes at {reset_at}."
)
THINKING_MESSAGE = "..."
EMPTY_RESPONSE_MESSAGE = "I could not generate a response this time. Please try again."
GENERIC_AI_ERROR_MESSAGE = "🐾 The cat is sleeping right now. Please try again later."
AI_TIMEOUT_MESSAGE = (
    "🐾 The cat is taking too long to wake up. Please try again in a moment."
)
IMAGE_PROVIDER_ERROR_MESSAGE = (
    "🐾 The cat could not process this photo. Please try again with a clearer image."
)
PLUS_PAYMENT_SUCCESS_MESSAGE = "CatGPT Plus is active for 30 days. Thank you."
PLUS_PAYMENT_INVALID_MESSAGE = "This payment could not be verified."
PLUS_ADMIN_GRANTED_MESSAGE = "⭐ An admin granted you CatGPT Plus. It is active until {expires_at}."
PLUS_ADMIN_REVOKED_MESSAGE = "❌ Your CatGPT Plus subscription was removed by an admin. Your account is now CatGPT Free."
PLUS_EXPIRED_MESSAGE = "⌛ Your CatGPT Plus subscription has expired. Your account is now CatGPT Free."
BLOCKED_BY_ADMIN_MESSAGE = "⛔️ Your access to CatGPT has been blocked by an admin."
UNBLOCKED_BY_ADMIN_MESSAGE = "✅ Your access to CatGPT has been restored by an admin."
GROUP_BLOCKED_BY_ADMIN_MESSAGE = "⛔️ This group has been blocked from using CatGPT by an admin."
GROUP_UNBLOCKED_BY_ADMIN_MESSAGE = "✅ This group has been unblocked and can use CatGPT again."

CATGPT_SYSTEM_PROMPT = (
    "You are CatGPT, a warm, helpful, intelligent AI assistant with a subtle cat personality. "
    "Be helpful, natural, and lightly playful without overdoing cat jokes."
)


def format_account_message(
    *,
    user_id: int,
    plan: str,
    used: int,
    limit: int,
    plus_daily_limit: int,
    plus_expires_at: int | None,
    support_url: str = "",
) -> str:
    is_admin = plan == "Admin"
    is_plus = plan == "Plus"
    subscription = "CatGPT Admin" if is_admin else ("CatGPT Plus" if is_plus else "CatGPT Free")
    lines = [f"Subscription: <b>{subscription}</b> ✔️"]

    if is_plus and plus_expires_at is not None:
        lines.append(f"📅 <b>Expires:</b> {_format_date(plus_expires_at)}")

    lines.extend(
        [
            "",
            f"👤 <b>User ID:</b> <code>{user_id}</code>",
            "",
            "📊 <b>Usage</b>",
            "",
            f"<b>Daily Requests:</b> {used}/{limit}",
            "<b>Reset:</b> 00:00 UTC",
            "",
        ]
    )

    if is_admin:
        lines.extend(
            [
                "👨‍✈️ <b>Admin Access:</b>",
                f"└ {plus_daily_limit} Daily Requests",
                "└ Admin Panel",
            ]
        )
    elif is_plus:
        lines.extend(
            [
                "🚀 <b>Plus Benefits:</b>",
                f"└ {plus_daily_limit} Daily Requests",
            ]
        )
    else:
        lines.extend(
            [
                "<b>Need more?</b> Upgrade to <b>Plus</b>",
                "",
                "🚀 <b>Plus Subscription:</b>",
                f"└ {plus_daily_limit} Daily Requests",
            ]
        )

    lines.extend(["", format_support_line(support_url)])
    return "\n".join(lines)


def format_support_line(support_url: str) -> str:
    if not support_url:
        return "📞 Support"
    
    url = support_url.strip()
    if url.isdigit():
        url = f"tg://user?id={url}"
    elif url.startswith("@"):
        url = f"https://t.me/{url[1:]}"
    elif not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
        if "/" not in url and "." not in url:
            url = f"https://t.me/{url}"
        elif url.startswith("t.me/"):
            url = f"https://{url}"

    safe_url = escape(url, quote=True)
    return f'📞 <a href="{safe_url}">Support</a>'


def _format_date(value: int) -> str:
    return datetime.fromtimestamp(value, tz=UTC).strftime("%Y-%m-%d")
