from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.database import CatGPTStorage
from bot.texts import PLUS_EXPIRED_MESSAGE

logger = logging.getLogger(__name__)

PLUS_EXPIRY_CHECK_SECONDS = 300


class PlusExpiryNotifier:
    def __init__(
        self,
        *,
        bot: Bot,
        storage: CatGPTStorage,
        interval_seconds: int = PLUS_EXPIRY_CHECK_SECONDS,
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.interval_seconds = max(60, int(interval_seconds))
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await self.notify_expired_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
                return
            except asyncio.TimeoutError:
                pass

    async def notify_expired_once(self) -> None:
        expired_users = await self.storage.list_expired_plus_to_notify()
        for user in expired_users:
            user_id = int(user["telegram_id"])
            try:
                await self.bot.send_message(chat_id=user_id, text=PLUS_EXPIRED_MESSAGE)
            except (TelegramBadRequest, TelegramForbiddenError):
                logger.warning("Could not notify expired Plus user %s", user_id)
            finally:
                await self.storage.mark_plus_expiry_notified(user_id)
