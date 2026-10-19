"""Webhook signature check (Standard Webhooks, https://www.standardwebhooks.com).

Same scheme as the server (seekgist_providers/webhooks.py). Pass the RAW body bytes exactly as
received; do not parse and re-serialize them first.

    event = verify_webhook(secret, request.headers, request.body)   # raises on a bad message
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any, TypedDict

from .errors import WebhookVerificationError

TOLERANCE_S = 300
PREFIX = "whsec_"


class WebhookEvent(TypedDict):
    """The JSON body SeekGist sends. `data` depends on `type` (see the docs, "Webhooks")."""

    id: str
    type: str
    created_at: str
    tenant_id: str
    data: dict[str, Any]


def _key(secret: str) -> bytes:
    raw = secret[len(PREFIX) :] if secret.startswith(PREFIX) else secret
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise WebhookVerificationError("the webhook secret is not valid base64 (expected whsec_...)") from e


def sign_webhook(secret: str, msg_id: str, timestamp: int, body: bytes | str) -> str:
    """`v1,<base64 HMAC-SHA256>` for one message. Useful to test your own receiver."""
    raw = body.encode() if isinstance(body, str) else body
    mac = hmac.new(_key(secret), f"{msg_id}.{timestamp}.".encode() + raw, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def verify_webhook(
    secret: str,
    headers: Mapping[str, str],
    body: bytes | str,
    *,
    tolerance_s: int = TOLERANCE_S,
    now: int | None = None,
) -> WebhookEvent:
    """Check a webhook and return its parsed body. Raises WebhookVerificationError when the
    headers are missing, the timestamp is more than `tolerance_s` away, or no signature matches
    (during a secret rotation the header holds two; any match is enough). Also drop repeated
    `id`s on your side: a delivery can come more than once."""
    h = {k.lower(): v for k, v in headers.items()}
    msg_id, ts_raw, sigs = h.get("webhook-id"), h.get("webhook-timestamp"), h.get("webhook-signature")
    if not (msg_id and ts_raw and sigs):
        raise WebhookVerificationError("missing webhook headers")
    try:
        ts = int(ts_raw)
    except ValueError as e:
        raise WebhookVerificationError("bad webhook timestamp") from e
    if abs((int(time.time()) if now is None else now) - ts) > tolerance_s:
        raise WebhookVerificationError("webhook timestamp is too old or too new")
    expected = sign_webhook(secret, msg_id, ts, body)
    if not any(hmac.compare_digest(expected, s.strip()) for s in sigs.split(" ")):
        raise WebhookVerificationError("no matching webhook signature")
    try:
        event: WebhookEvent = json.loads(body)
    except ValueError as e:
        raise WebhookVerificationError("the webhook body is not JSON") from e
    return event
