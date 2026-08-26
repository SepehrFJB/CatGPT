from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from bot.config import sqlite_path_from_url

SECONDS_PER_DAY = 86_400
USAGE_EVENT_RETENTION_SECONDS = 7 * SECONDS_PER_DAY
ASSISTANT_TURN_RETENTION_SECONDS = 30 * SECONDS_PER_DAY


@dataclass(frozen=True)
class QuotaResult:
    allowed: bool
    used: int
    limit: int
    reset_at: int | None
    is_plus: bool
    event_id: int | None = None


@dataclass(frozen=True)
class AccountSnapshot:
    user_id: int
    plan: str
    used: int
    limit: int
    reset_at: int | None
    plus_expires_at: int | None


class CatGPTStorage:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db
        self._lock = asyncio.Lock()

    @classmethod
    async def connect(cls, database_url: str) -> "CatGPTStorage":
        db = await aiosqlite.connect(sqlite_path_from_url(database_url))
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        return cls(db)

    async def close(self) -> None:
        await self.db.close()

    async def init_schema(self) -> None:
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_bot INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                plus_expires_at INTEGER,
                plus_expiry_notified_at INTEGER,
                last_user_text TEXT,
                last_assistant_text TEXT,
                last_turn_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                chat_id INTEGER,
                created_at INTEGER NOT NULL,
                kind TEXT NOT NULL DEFAULT 'ai_message',
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_usage_user_created
            ON usage_events (telegram_id, created_at);

            CREATE TABLE IF NOT EXISTS chat_histories (
                telegram_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                assistant_message_id INTEGER,
                last_user_text TEXT NOT NULL,
                last_assistant_text TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, chat_id),
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chat_histories_assistant_message
            ON chat_histories (chat_id, assistant_message_id);

            CREATE TABLE IF NOT EXISTS assistant_message_turns (
                chat_id INTEGER NOT NULL,
                assistant_message_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                last_user_text TEXT NOT NULL,
                last_assistant_text TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, assistant_message_id),
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_assistant_message_turns_user_chat
            ON assistant_message_turns (telegram_id, chat_id, created_at);

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                currency TEXT NOT NULL,
                total_amount INTEGER NOT NULL,
                telegram_payment_charge_id TEXT NOT NULL,
                provider_payment_charge_id TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS group_chats (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                title TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_admins (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS required_channels (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                title TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS blocked_targets (
                target_id INTEGER PRIMARY KEY,
                target_type TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """
        )
        await self._ensure_column("users", "plus_expiry_notified_at", "INTEGER")
        await self._deduplicate_payment_charge_ids()
        await self.db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_telegram_charge_id
            ON payments (telegram_payment_charge_id)
            """
        )
        await self.db.commit()
        await self.cleanup_runtime_rows()

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = await self.db.execute_fetchall(f"PRAGMA table_info({table})")
        existing = {str(row["name"]) for row in rows}
        if column in existing:
            return
        await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def _deduplicate_payment_charge_ids(self) -> None:
        await self.db.execute(
            """
            DELETE FROM payments
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM payments
                GROUP BY telegram_payment_charge_id
            )
            """
        )

    async def sync_owner_admins(self, owner_ids: frozenset[int]) -> None:
        now = unix_now()
        normalized_owner_ids = frozenset(int(user_id) for user_id in owner_ids)
        if normalized_owner_ids:
            placeholders = ", ".join("?" for _ in normalized_owner_ids)
            await self.db.execute(
                f"""
                UPDATE bot_admins
                SET role = 'admin'
                WHERE role = 'owner' AND user_id NOT IN ({placeholders})
                """,
                tuple(normalized_owner_ids),
            )
        else:
            await self.db.execute("UPDATE bot_admins SET role = 'admin' WHERE role = 'owner'")

        for user_id in normalized_owner_ids:
            await self.db.execute(
                """
                INSERT INTO bot_admins (user_id, role, created_at)
                VALUES (?, 'owner', ?)
                ON CONFLICT(user_id) DO UPDATE SET role = 'owner'
                """,
                (int(user_id), now),
            )
        await self.db.commit()

    async def cleanup_runtime_rows(self, now: int | None = None) -> None:
        now = unix_now() if now is None else now
        await self.db.execute(
            "DELETE FROM usage_events WHERE created_at < ?",
            (now - USAGE_EVENT_RETENTION_SECONDS,),
        )
        await self.db.execute(
            "DELETE FROM assistant_message_turns WHERE created_at < ?",
            (now - ASSISTANT_TURN_RETENTION_SECONDS,),
        )
        await self.db.commit()

    async def upsert_user(self, user: Any) -> None:
        if user is None:
            return
        now = unix_now()
        await self.db.execute(
            """
            INSERT INTO users (
                telegram_id, username, first_name, last_name, is_bot, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                is_bot = excluded.is_bot,
                updated_at = excluded.updated_at
            """,
            (
                int(user.id),
                getattr(user, "username", None),
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
                1 if getattr(user, "is_bot", False) else 0,
                now,
                now,
            ),
        )
        await self.db.commit()

    async def upsert_group_chat(self, chat: Any) -> None:
        if chat is None or str(getattr(chat, "type", "")) not in {"group", "supergroup"}:
            return
        now = unix_now()
        await self.db.execute(
            """
            INSERT INTO group_chats (chat_id, username, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                username = excluded.username,
                title = excluded.title,
                updated_at = excluded.updated_at
            """,
            (
                int(chat.id),
                getattr(chat, "username", None),
                getattr(chat, "title", None),
                now,
                now,
            ),
        )
        await self.db.commit()

    async def is_bot_admin(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        row = await self._fetchone("SELECT user_id FROM bot_admins WHERE user_id = ?", (int(user_id),))
        return row is not None

    async def get_bot_admin_role(self, user_id: int | None) -> str | None:
        if user_id is None:
            return None
        row = await self._fetchone("SELECT role FROM bot_admins WHERE user_id = ?", (int(user_id),))
        return str(row["role"]) if row else None

    async def list_bot_admins(self) -> list[dict[str, Any]]:
        rows = await self.db.execute_fetchall(
            """
            SELECT user_id, role, created_at
            FROM bot_admins
            ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END, user_id ASC
            """
        )
        return [dict(row) for row in rows]

    async def add_bot_admin(self, user_id: int) -> None:
        now = unix_now()
        await self.db.execute(
            """
            INSERT INTO bot_admins (user_id, role, created_at)
            VALUES (?, 'admin', ?)
            ON CONFLICT(user_id) DO UPDATE SET
                role = CASE WHEN role = 'owner' THEN 'owner' ELSE 'admin' END
            """,
            (int(user_id), now),
        )
        await self.db.commit()

    async def remove_bot_admin(self, user_id: int) -> bool:
        row = await self._fetchone("SELECT role FROM bot_admins WHERE user_id = ?", (int(user_id),))
        if row is None or str(row["role"]) == "owner":
            return False
        await self.db.execute("DELETE FROM bot_admins WHERE user_id = ?", (int(user_id),))
        await self.db.commit()
        return True

    async def get_user_admin_profile(
        self,
        telegram_id: int,
        free_limit: int,
        plus_limit: int,
        now: int | None = None,
    ) -> dict[str, Any] | None:
        now = unix_now() if now is None else now
        row = await self._fetchone("SELECT * FROM users WHERE telegram_id = ?", (int(telegram_id),))
        if row is None:
            return None
        snapshot = await self.account_snapshot(
            int(telegram_id),
            free_limit,
            plus_limit,
            now=now,
            is_admin=await self.is_bot_admin(telegram_id),
        )
        payment_row = await self._fetchone(
            "SELECT COUNT(*) AS count FROM payments WHERE telegram_id = ?",
            (int(telegram_id),),
        )
        return {
            **dict(row),
            "plan": snapshot.plan,
            "daily_used": snapshot.used,
            "daily_limit": snapshot.limit,
            "payment_count": int(payment_row["count"] if payment_row else 0),
            "is_blocked": await self.is_blocked(telegram_id, None),
        }

    async def admin_stats(self, free_limit: int, plus_limit: int, now: int | None = None) -> dict[str, int | str]:
        now = unix_now() if now is None else now
        cutoff = utc_day_start(now)
        reset_at = next_utc_midnight(now)
        users = await self._count("SELECT COUNT(*) FROM users")
        groups = await self._count("SELECT COUNT(*) FROM group_chats")
        plus_users = await self._count("SELECT COUNT(*) FROM users WHERE plus_expires_at > ?", (now,))
        requests_today = await self._count(
            "SELECT COUNT(*) FROM usage_events WHERE created_at >= ? AND created_at < ?",
            (cutoff, reset_at),
        )
        payments = await self._count("SELECT COUNT(*) FROM payments")
        blocked = await self._count("SELECT COUNT(*) FROM blocked_targets")
        channels = await self._count("SELECT COUNT(*) FROM required_channels")
        force_sub_enabled = await self.is_force_sub_enabled()
        bot_paused = await self.is_bot_paused()
        return {
            "users": users,
            "groups": groups,
            "plus_users": plus_users,
            "requests_today": requests_today,
            "payments": payments,
            "blocked": blocked,
            "channels": channels,
            "free_limit": free_limit,
            "plus_limit": plus_limit,
            "force_sub": "Enabled" if force_sub_enabled else "Disabled",
            "bot_paused": bot_paused,
        }

    async def list_user_ids(self) -> list[int]:
        rows = await self.db.execute_fetchall("SELECT telegram_id FROM users ORDER BY telegram_id ASC")
        return [int(row["telegram_id"]) for row in rows]

    async def list_group_chat_ids(self) -> list[int]:
        rows = await self.db.execute_fetchall("SELECT chat_id FROM group_chats ORDER BY chat_id ASC")
        return [int(row["chat_id"]) for row in rows]

    async def list_plus_users(self, now: int | None = None) -> list[dict[str, Any]]:
        now = unix_now() if now is None else now
        rows = await self.db.execute_fetchall(
            """
            SELECT telegram_id, username, first_name, last_name, plus_expires_at, created_at
            FROM users
            WHERE plus_expires_at > ?
            ORDER BY plus_expires_at ASC
            """,
            (now,),
        )
        return [
            {
                "telegram_id": int(row["telegram_id"]),
                "username": row["username"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "plus_expires_at": row["plus_expires_at"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def is_bot_paused(self) -> bool:
        row = await self._fetchone("SELECT value FROM app_settings WHERE key = 'bot_paused'")
        return bool(row and str(row["value"]) == "1")

    async def set_bot_paused(self, paused: bool) -> None:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('bot_paused', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            ("1" if paused else "0", unix_now()),
        )
        await self.db.commit()

    async def is_force_sub_enabled(self) -> bool:
        row = await self._fetchone("SELECT value FROM app_settings WHERE key = 'force_sub_enabled'")
        return bool(row and str(row["value"]) == "1")

    async def set_force_sub_enabled(self, enabled: bool) -> None:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('force_sub_enabled', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            ("1" if enabled else "0", unix_now()),
        )
        await self.db.commit()

    async def add_required_channel(self, chat_id: int, username: str | None, title: str | None) -> None:
        await self.db.execute(
            """
            INSERT INTO required_channels (chat_id, username, title, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                username = excluded.username,
                title = excluded.title
            """,
            (int(chat_id), username, title, unix_now()),
        )
        await self.db.commit()

    async def remove_required_channel(self, chat_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM required_channels WHERE chat_id = ?", (int(chat_id),))
        await self.db.commit()
        return cursor.rowcount > 0

    async def list_required_channels(self) -> list[dict[str, Any]]:
        rows = await self.db.execute_fetchall(
            "SELECT chat_id, username, title, created_at FROM required_channels ORDER BY created_at ASC"
        )
        return [dict(row) for row in rows]

    async def add_blocked_target(self, target_id: int) -> str:
        target_type = "group" if int(target_id) < 0 else "user"
        await self.db.execute(
            """
            INSERT INTO blocked_targets (target_id, target_type, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET target_type = excluded.target_type
            """,
            (int(target_id), target_type, unix_now()),
        )
        await self.db.commit()
        return target_type

    async def remove_blocked_target(self, target_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM blocked_targets WHERE target_id = ?", (int(target_id),))
        await self.db.commit()
        return cursor.rowcount > 0

    async def list_blocked_targets(self) -> list[dict[str, Any]]:
        rows = await self.db.execute_fetchall(
            "SELECT target_id, target_type, created_at FROM blocked_targets ORDER BY created_at DESC"
        )
        return [dict(row) for row in rows]

    async def is_blocked(self, user_id: int | None, chat_id: int | None = None) -> bool:
        target_ids = [int(value) for value in (user_id, chat_id) if value is not None]
        if not target_ids:
            return False
        placeholders = ", ".join("?" for _ in target_ids)
        row = await self._fetchone(
            f"SELECT target_id FROM blocked_targets WHERE target_id IN ({placeholders}) LIMIT 1",
            tuple(target_ids),
        )
        return row is not None

    async def get_last_turn(self, telegram_id: int, chat_id: int | None = None) -> tuple[str, str] | None:
        if chat_id is not None:
            row = await self._fetchone(
                """
                SELECT last_user_text, last_assistant_text
                FROM chat_histories
                WHERE telegram_id = ? AND chat_id = ?
                """,
                (telegram_id, chat_id),
            )
            if row is None or not row["last_user_text"] or not row["last_assistant_text"]:
                return None
            return row["last_user_text"], row["last_assistant_text"]

        row = await self._fetchone(
            """
            SELECT last_user_text, last_assistant_text
            FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
        if row is None or not row["last_user_text"] or not row["last_assistant_text"]:
            return None
        return row["last_user_text"], row["last_assistant_text"]

    async def get_turn_by_assistant_message(
        self,
        chat_id: int,
        assistant_message_id: int,
    ) -> tuple[str, str] | None:
        row = await self._fetchone(
            """
            SELECT last_user_text, last_assistant_text
            FROM assistant_message_turns
            WHERE chat_id = ? AND assistant_message_id = ?
            """,
            (chat_id, assistant_message_id),
        )
        if row is not None and row["last_user_text"] and row["last_assistant_text"]:
            return row["last_user_text"], row["last_assistant_text"]

        row = await self._fetchone(
            """
            SELECT last_user_text, last_assistant_text
            FROM chat_histories
            WHERE chat_id = ? AND assistant_message_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (chat_id, assistant_message_id),
        )
        if row is None or not row["last_user_text"] or not row["last_assistant_text"]:
            return None
        return row["last_user_text"], row["last_assistant_text"]

    async def save_last_turn(
        self,
        telegram_id: int,
        user_text: str,
        assistant_text: str,
        chat_id: int | None = None,
        assistant_message_id: int | None = None,
        assistant_message_ids: list[int] | None = None,
    ) -> None:
        now = unix_now()
        message_ids = normalize_message_ids(assistant_message_id, assistant_message_ids)
        await self.db.execute(
            """
            UPDATE users
            SET last_user_text = ?, last_assistant_text = ?, last_turn_at = ?, updated_at = ?
            WHERE telegram_id = ?
            """,
            (user_text, assistant_text, now, now, telegram_id),
        )
        if chat_id is not None:
            await self.db.execute(
                """
                INSERT INTO chat_histories (
                    telegram_id, chat_id, assistant_message_id,
                    last_user_text, last_assistant_text, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id, chat_id) DO UPDATE SET
                    assistant_message_id = excluded.assistant_message_id,
                    last_user_text = excluded.last_user_text,
                    last_assistant_text = excluded.last_assistant_text,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, chat_id, message_ids[0] if message_ids else None, user_text, assistant_text, now),
            )
            for message_id in message_ids:
                await self.db.execute(
                    """
                    INSERT INTO assistant_message_turns (
                        chat_id, assistant_message_id, telegram_id,
                        last_user_text, last_assistant_text, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id, assistant_message_id) DO UPDATE SET
                        telegram_id = excluded.telegram_id,
                        last_user_text = excluded.last_user_text,
                        last_assistant_text = excluded.last_assistant_text,
                        created_at = excluded.created_at
                    """,
                    (chat_id, message_id, telegram_id, user_text, assistant_text, now),
                )
        await self.db.commit()

    async def try_consume_quota(
        self,
        telegram_id: int,
        chat_id: int,
        free_limit: int,
        plus_limit: int,
        now: int | None = None,
        is_admin: bool = False,
    ) -> QuotaResult:
        now = unix_now() if now is None else now
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                is_plus = is_admin or await self._is_plus_active_unlocked(telegram_id, now)
                limit = plus_limit if is_plus else free_limit
                cutoff = utc_day_start(now)
                reset_at = next_utc_midnight(now)
                rows = await self.db.execute_fetchall(
                    """
                    SELECT id, created_at
                    FROM usage_events
                    WHERE telegram_id = ? AND created_at >= ? AND created_at < ?
                    ORDER BY created_at ASC
                    """,
                    (telegram_id, cutoff, reset_at),
                )
                used = len(rows)
                if used >= limit:
                    await self.db.execute("COMMIT")
                    return QuotaResult(
                        allowed=False,
                        used=used,
                        limit=limit,
                        reset_at=reset_at,
                        is_plus=is_plus,
                    )

                cursor = await self.db.execute(
                    """
                    INSERT INTO usage_events (telegram_id, chat_id, created_at, kind)
                    VALUES (?, ?, ?, 'ai_message')
                    """,
                    (telegram_id, chat_id, now),
                )
                await self.db.execute("COMMIT")
                return QuotaResult(
                    allowed=True,
                    used=used + 1,
                    limit=limit,
                    reset_at=reset_at,
                    is_plus=is_plus,
                    event_id=cursor.lastrowid,
                )
            except Exception:
                await self.db.execute("ROLLBACK")
                raise

    async def refund_usage_event(self, event_id: int | None) -> None:
        if event_id is None:
            return
        await self.db.execute("DELETE FROM usage_events WHERE id = ?", (event_id,))
        await self.db.commit()

    async def account_snapshot(
        self,
        telegram_id: int,
        free_limit: int,
        plus_limit: int,
        now: int | None = None,
        is_admin: bool = False,
    ) -> AccountSnapshot:
        now = unix_now() if now is None else now
        row = await self._fetchone(
            "SELECT plus_expires_at FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        plus_expires_at = row["plus_expires_at"] if row else None
        is_plus = bool(plus_expires_at and plus_expires_at > now)
        limit = plus_limit if is_admin or is_plus else free_limit
        cutoff = utc_day_start(now)
        reset_at = next_utc_midnight(now)
        rows = await self.db.execute_fetchall(
            """
            SELECT created_at
            FROM usage_events
            WHERE telegram_id = ? AND created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            """,
            (telegram_id, cutoff, reset_at),
        )
        return AccountSnapshot(
            user_id=telegram_id,
            plan="Admin" if is_admin else ("Plus" if is_plus else "Free"),
            used=len(rows),
            limit=limit,
            reset_at=reset_at,
            plus_expires_at=plus_expires_at,
        )

    async def activate_plus(self, telegram_id: int, period_seconds: int, now: int | None = None) -> int:
        now = unix_now() if now is None else now
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetchone(
                    "SELECT plus_expires_at FROM users WHERE telegram_id = ?",
                    (telegram_id,),
                )
                base = now
                if row and row["plus_expires_at"] and row["plus_expires_at"] > now:
                    base = int(row["plus_expires_at"])
                expires_at = base + period_seconds
                await self.db.execute(
                    """
                    UPDATE users
                    SET plus_expires_at = ?, plus_expiry_notified_at = NULL, updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (expires_at, now, telegram_id),
                )
                await self.db.execute("COMMIT")
                return expires_at
            except Exception:
                await self.db.execute("ROLLBACK")
                raise

    async def revoke_plus(self, telegram_id: int, now: int | None = None) -> None:
        now = unix_now() if now is None else now
        await self.db.execute(
            """
            UPDATE users
            SET plus_expires_at = NULL, plus_expiry_notified_at = ?, updated_at = ?
            WHERE telegram_id = ?
            """,
            (now, now, int(telegram_id)),
        )
        await self.db.commit()

    async def list_expired_plus_to_notify(self, now: int | None = None) -> list[dict[str, Any]]:
        now = unix_now() if now is None else now
        rows = await self.db.execute_fetchall(
            """
            SELECT telegram_id, plus_expires_at
            FROM users
            WHERE plus_expires_at IS NOT NULL
                AND plus_expires_at <= ?
                AND plus_expiry_notified_at IS NULL
            ORDER BY plus_expires_at ASC
            """,
            (now,),
        )
        return [dict(row) for row in rows]

    async def mark_plus_expiry_notified(self, telegram_id: int, now: int | None = None) -> None:
        now = unix_now() if now is None else now
        await self.db.execute(
            """
            UPDATE users
            SET plus_expiry_notified_at = ?, updated_at = ?
            WHERE telegram_id = ?
            """,
            (now, now, int(telegram_id)),
        )
        await self.db.commit()

    async def record_payment(
        self,
        telegram_id: int,
        payload: str,
        currency: str,
        total_amount: int,
        telegram_payment_charge_id: str,
        provider_payment_charge_id: str | None,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO payments (
                telegram_id, payload, currency, total_amount,
                telegram_payment_charge_id, provider_payment_charge_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                payload,
                currency,
                total_amount,
                telegram_payment_charge_id,
                provider_payment_charge_id,
                unix_now(),
            ),
        )
        await self.db.commit()

    async def record_payment_and_activate_plus(
        self,
        telegram_id: int,
        payload: str,
        currency: str,
        total_amount: int,
        telegram_payment_charge_id: str,
        provider_payment_charge_id: str | None,
        period_seconds: int,
        now: int | None = None,
    ) -> tuple[bool, int | None]:
        now = unix_now() if now is None else now
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.db.execute(
                    """
                    INSERT OR IGNORE INTO payments (
                        telegram_id, payload, currency, total_amount,
                        telegram_payment_charge_id, provider_payment_charge_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        telegram_id,
                        payload,
                        currency,
                        total_amount,
                        telegram_payment_charge_id,
                        provider_payment_charge_id,
                        now,
                    ),
                )
                if cursor.rowcount == 0:
                    row = await self._fetchone(
                        "SELECT plus_expires_at FROM users WHERE telegram_id = ?",
                        (telegram_id,),
                    )
                    await self.db.execute("COMMIT")
                    return False, int(row["plus_expires_at"]) if row and row["plus_expires_at"] else None

                row = await self._fetchone(
                    "SELECT plus_expires_at FROM users WHERE telegram_id = ?",
                    (telegram_id,),
                )
                base = now
                if row and row["plus_expires_at"] and row["plus_expires_at"] > now:
                    base = int(row["plus_expires_at"])
                expires_at = base + period_seconds
                await self.db.execute(
                    """
                    UPDATE users
                    SET plus_expires_at = ?, plus_expiry_notified_at = NULL, updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (expires_at, now, telegram_id),
                )
                await self.db.execute("COMMIT")
                return True, expires_at
            except Exception:
                await self.db.execute("ROLLBACK")
                raise

    async def _is_plus_active_unlocked(self, telegram_id: int, now: int) -> bool:
        row = await self._fetchone(
            "SELECT plus_expires_at FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        return bool(row and row["plus_expires_at"] and row["plus_expires_at"] > now)

    async def _fetchone(self, sql: str, parameters: tuple = ()) -> aiosqlite.Row | None:
        async with self.db.execute(sql, parameters) as cursor:
            return await cursor.fetchone()

    async def _count(self, sql: str, parameters: tuple = ()) -> int:
        row = await self._fetchone(sql, parameters)
        if row is None:
            return 0
        return int(row[0])


def unix_now() -> int:
    return int(time.time())


def utc_day_start(value: int) -> int:
    return (value // SECONDS_PER_DAY) * SECONDS_PER_DAY


def next_utc_midnight(value: int) -> int:
    return utc_day_start(value) + SECONDS_PER_DAY


def normalize_message_ids(
    assistant_message_id: int | None,
    assistant_message_ids: list[int] | None,
) -> list[int]:
    values: list[int] = []
    if assistant_message_ids:
        values.extend(message_id for message_id in assistant_message_ids if isinstance(message_id, int))
    if isinstance(assistant_message_id, int):
        values.append(assistant_message_id)

    seen: set[int] = set()
    normalized: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def format_timestamp(value: int | None) -> str:
    if value is None:
        return "Not scheduled"
    return datetime.fromtimestamp(value, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
