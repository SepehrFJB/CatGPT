from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message, PreCheckoutQuery

from bot.config import Settings
from bot.database import CatGPTStorage
from bot.services.payments import PLUS_CURRENCY, parse_plus_payload
from bot.texts import PLUS_PAYMENT_INVALID_MESSAGE, PLUS_PAYMENT_SUCCESS_MESSAGE

router = Router(name="payments")


@router.pre_checkout_query()
async def pre_checkout_plus(query: PreCheckoutQuery, settings: Settings) -> None:
    payload = parse_plus_payload(query.invoice_payload)
    if (
        payload is None
        or payload.user_id != query.from_user.id
        or query.currency != PLUS_CURRENCY
        or query.total_amount != settings.plus_stars_price
    ):
        await query.answer(ok=False, error_message=PLUS_PAYMENT_INVALID_MESSAGE)
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_plus_payment(
    message: Message,
    settings: Settings,
    app_storage: CatGPTStorage,
) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    payment = message.successful_payment
    payload = parse_plus_payload(payment.invoice_payload)
    if payload is None or payload.user_id != message.from_user.id or payment.currency != PLUS_CURRENCY:
        await message.answer(PLUS_PAYMENT_INVALID_MESSAGE)
        return

    await app_storage.upsert_user(message.from_user)
    await app_storage.record_payment_and_activate_plus(
        telegram_id=message.from_user.id,
        payload=payment.invoice_payload,
        currency=payment.currency,
        total_amount=payment.total_amount,
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
        provider_payment_charge_id=payment.provider_payment_charge_id,
        period_seconds=settings.plus_period_seconds,
    )
    await message.answer(PLUS_PAYMENT_SUCCESS_MESSAGE)
