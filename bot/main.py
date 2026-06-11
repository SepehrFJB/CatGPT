from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from bot.config import Settings, SettingsError
from bot.database import CatGPTStorage
from bot.handlers import admin, payments, public
from bot.services.ai import CatGPTAI
from bot.services.backup import DatabaseBackupService
from bot.services.plus_expiry import PlusExpiryNotifier


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    app_storage = await CatGPTStorage.connect(settings.database_url)
    await app_storage.init_schema()
    await app_storage.sync_owner_admins(settings.admin_ids)

    bot = Bot(token=settings.bot_token)
    ai_service = CatGPTAI(settings)
    backup_service = DatabaseBackupService(
        bot=bot,
        storage=app_storage,
        database_url=settings.database_url,
    )
    plus_expiry_notifier = PlusExpiryNotifier(bot=bot, storage=app_storage)
    dispatcher = Dispatcher(
        settings=settings,
        app_storage=app_storage,
        ai_service=ai_service,
        backup_service=backup_service,
        plus_expiry_notifier=plus_expiry_notifier,
    )
    dispatcher.include_router(admin.router)
    dispatcher.include_router(payments.router)
    dispatcher.include_router(public.router)

    backup_service.start()
    plus_expiry_notifier.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await plus_expiry_notifier.stop()
        await backup_service.stop()
        await app_storage.close()
        await ai_service.close()
        await bot.session.close()


def run() -> None:
    try:
        asyncio.run(main())
    except SettingsError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        print("Bot stopped by user.", file=sys.stderr)
