from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import WebhookConfig


@dataclass(frozen=True)
class SignedWebhookPayload:
    timestamp: str
    signature: str
    body: bytes


def build_signed_payload(payload: dict[str, Any], secret: str, timestamp: int | None = None) -> SignedWebhookPayload:
    unix_ts = timestamp if timestamp is not None else int(time.time())
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signed = f"{unix_ts}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return SignedWebhookPayload(timestamp=str(unix_ts), signature=f"sha256={digest}", body=body)


class WebhookClient:
    def __init__(self, config: WebhookConfig):
        self.config = config

    def send_release(self, payload: dict[str, Any]) -> None:
        signed = build_signed_payload(payload, self.config.shared_secret)
        with httpx.Client(timeout=self.config.timeout_seconds, verify=self.config.verify_tls) as client:
            response = client.post(
                self.config.url,
                content=signed.body,
                headers={
                    "Content-Type": "application/json",
                    "X-Release-Timestamp": signed.timestamp,
                    "X-Release-Signature": signed.signature,
                },
            )
        response.raise_for_status()
