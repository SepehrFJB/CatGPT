from __future__ import annotations

import uuid
from dataclasses import dataclass

PLUS_PAYLOAD_PREFIX = "catgpt_plus"
PLUS_CURRENCY = "XTR"
PLUS_TITLE = "CatGPT Plus"
PLUS_DESCRIPTION = "30 days of higher CatGPT daily limits."
PLUS_PRICE_LABEL = "CatGPT Plus"


@dataclass(frozen=True)
class PlusPayload:
    user_id: int
    nonce: str


def make_plus_payload(user_id: int, nonce: str | None = None) -> str:
    return f"{PLUS_PAYLOAD_PREFIX}:{user_id}:{nonce or uuid.uuid4().hex}"


def parse_plus_payload(payload: str) -> PlusPayload | None:
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != PLUS_PAYLOAD_PREFIX:
        return None
    try:
        user_id = int(parts[1])
    except ValueError:
        return None
    if not parts[2]:
        return None
    return PlusPayload(user_id=user_id, nonce=parts[2])

