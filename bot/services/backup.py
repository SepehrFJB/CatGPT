from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from datetime import UTC, datetime, time as datetime_time, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, ReplyKeyboardMarkup

from bot.config import sqlite_path_from_url
from bot.database import CatGPTStorage

logger = logging.getLogger(__name__)


class DatabaseBackupService:
    def __init__(
        self,
        *,
        bot: Bot,
        storage: CatGPTStorage,
        database_url: str,
        backup_dir: Path | str = "backups",
        keep_count: int = 10,
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.database_path = resolve_database_path(database_url)
        self.backup_dir = Path(backup_dir)
        self.keep_count = max(1, int(keep_count))
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def send_backup_to_chat(
        self,
        chat_id: int,
        *,
        manual: bool,
        reply_markup: ReplyKeyboardMarkup | None = None,
    ) -> Path:
        await self.storage.cleanup_runtime_rows()
        backup_file = await asyncio.to_thread(self._create_backup_file)
        try:
            await self._send_backup_file(
                chat_id=chat_id,
                backup_file=backup_file,
                manual=manual,
                reply_markup=reply_markup,
            )
            return backup_file
        finally:
            await asyncio.to_thread(self._cleanup_old_backups)

    async def send_backup_to_owners(self, *, manual: bool = False) -> None:
        await self.storage.cleanup_runtime_rows()
        backup_file = await asyncio.to_thread(self._create_backup_file)
        try:
            owners = await self._owner_ids()
            for owner_id in owners:
                try:
                    await self._send_backup_file(
                        chat_id=owner_id,
                        backup_file=backup_file,
                        manual=manual,
                    )
                except Exception:
                    logger.exception("Failed to send database backup to owner %s", owner_id)
        finally:
            await asyncio.to_thread(self._cleanup_old_backups)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=seconds_until_next_utc_midnight())
                return
            except asyncio.TimeoutError:
                pass

            try:
                await self.send_backup_to_owners()
                logger.info("Automatic database backup sent to owners")
            except Exception:
                logger.exception("Automatic database backup failed")

    def _create_backup_file(self) -> Path:
        if self.database_path == Path(":memory:"):
            raise RuntimeError("In-memory databases cannot be backed up as a file.")
        if not self.database_path.exists():
            raise FileNotFoundError(f"Database file not found: {self.database_path}")

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_path = self.backup_dir / f"catgpt-{stamp}.sqlite3"
        with sqlite3.connect(str(self.database_path)) as source:
            with sqlite3.connect(str(backup_path)) as target:
                source.backup(target)

        if not backup_path.exists() or backup_path.stat().st_size <= 0:
            raise RuntimeError("Database backup file is empty or missing.")
        return backup_path

    async def _send_backup_file(
        self,
        *,
        chat_id: int,
        backup_file: Path,
        manual: bool,
        reply_markup: ReplyKeyboardMarkup | None = None,
    ) -> None:
        title = "Manual database backup" if manual else "Automatic daily database backup"
        caption = "\n".join(
            [
                f"🗂 {title}",
                f"File: {backup_file.name}",
                f"Size: {format_kilobytes(backup_file.stat().st_size)}",
                f"UTC: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}",
            ]
        )
        await self.bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(str(backup_file), filename=backup_file.name),
            caption=caption,
            reply_markup=reply_markup,
        )

    async def _owner_ids(self) -> list[int]:
        admins = await self.storage.list_bot_admins()
        return sorted(
            int(item["user_id"])
            for item in admins
            if str(item.get("role") or "").strip().lower() == "owner"
        )

    def _cleanup_old_backups(self) -> None:
        backups = sorted(
            (path for path in self.backup_dir.glob("catgpt-*.sqlite3") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale in backups[self.keep_count :]:
            with contextlib.suppress(OSError):
                stale.unlink()


def resolve_database_path(database_url: str) -> Path:
    raw_path = sqlite_path_from_url(database_url)
    if raw_path == ":memory:":
        return Path(":memory:")
    path = Path(raw_path)
    return path if path.is_absolute() else Path.cwd() / path


def format_kilobytes(byte_count: int) -> str:
    kilobytes = byte_count / 1024
    if kilobytes.is_integer():
        return f"{int(kilobytes)} KB"
    return f"{kilobytes:.1f} KB"


def seconds_until_next_utc_midnight(now: datetime | None = None) -> float:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    next_midnight = datetime.combine(current.date() + timedelta(days=1), datetime_time.min, tzinfo=UTC)
    return max(1.0, (next_midnight - current).total_seconds())
