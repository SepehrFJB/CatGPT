# CatGPT Telegram Bot

CatGPT is an English Telegram AI bot built with aiogram and the OpenAI Python SDK against the official OpenAI-compatible DeepSeek endpoint.

[Try the live bot](https://t.me/CatGPTAIBot)

## Setup

1. Create a virtual environment.
2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your real values.
4. Run the bot:

```powershell
python -m bot
```

## Features

- DeepSeek V4 Flash through `openai.AsyncOpenAI` for text chat over HTTP/2.
- Photo and attachment handling is blocked at the Telegram bot layer; AI requests are text-only.
- Streaming via Telegram `sendMessageDraft`, with a final saved message.
- Text input.
- Conversation history includes only the previous user/assistant turn. In groups, history is scoped by user and chat; replying to a bot answer continues that answer's thread.
- Group support: when the bot is in a group, it answers replies to the bot or messages that mention the bot username.
- Guest Mode support through Telegram `guest_message`; reply context is used when Telegram provides it, but normal non-reply guest messages do not have stored history.
- Free and Plus daily message limits reset at 00:00 UTC.
- Telegram Stars 30-day Plus subscription flow.
- Admin panel for stats, user inspection, broadcasts, admins, force join, blocking, and database backups.

## Environment Variables

Create a `.env` file from `.env.example` and fill in the real values.

- `BOT_TOKEN`: Telegram bot token from BotFather (required)
- `DEEPSEEK_API_KEY`: API key for the DeepSeek/OpenAI-compatible endpoint (required)
- `DEEPSEEK_BASE_URL`: OpenAI-compatible API base URL
- `DEEPSEEK_MODEL`: model name used for chat completions
- `ADMIN_IDS`: comma-separated Telegram user IDs for admin panel access
- `SUPPORT_URL`: support link shown in My Account, such as `tg://user?id=123456789`
- `FREE_DAILY_LIMIT`: daily message limit for free users
- `PLUS_DAILY_LIMIT`: daily message limit for Plus users and admins
- `PLUS_STARS_PRICE`: Telegram Stars price for Plus
- `PLUS_PERIOD_SECONDS`: Plus duration in seconds
- `MAX_INPUT_CHARS`: maximum user text length
- `MAX_OUTPUT_TOKENS`: maximum AI output tokens
- `DATABASE_URL`: SQLite database URL
- `GROUP_WELCOME_PHOTO`: optional local path or Telegram file ID for the group welcome photo

## Commands
- `/start` start + register user
- `/account` account status
- Admin panel is shown to admins after `/start`

Group behavior:

- Slash commands are not executed in groups.
- In groups, CatGPT only answers normal text when the bot is mentioned or when a user replies to a bot message.
- Text that starts with `/` is ignored in groups, even if the bot is mentioned or replied to.

## Notes

- Keep real API keys in `.env`; do not commit them.
- Telegram chat wallpaper, bot avatar, and visual profile styling are configured through Telegram/BotFather or the client, not bot code.
- Tool calls are explicitly disabled with `tool_choice="none"`; no web search options or tools are sent to the API.
- Photo analysis is disabled for now.
- DeepSeek context caching is enabled by default by DeepSeek. CatGPT requests streamed usage data and logs `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` when DeepSeek returns them.
- The OpenAI SDK default timeout and retry behavior is used for DeepSeek requests.
