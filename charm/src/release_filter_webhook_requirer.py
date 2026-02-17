from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import constants as c
from models import ReconcileError, WebhookResolution


class ReleaseFilterWebhookRequirer:
    def __init__(
        self,
        *,
        relations: list[Any],
        read_secret_content: Callable[[str], dict[str, str]],
    ):
        self._relations = relations
        self._read_secret_content = read_secret_content

    def resolve(self, config: Mapping[str, Any]) -> WebhookResolution:
        relations = sorted(self._relations, key=lambda rel: rel.id)
        if relations:
            relation = relations[0]
            if relation.app is None:
                raise ReconcileError("invalid relation contract: missing remote application")
            remote_data = relation.data[relation.app]
            protocol_version = str(remote_data.get("protocol_version", "")).strip()
            if protocol_version != c.PROTOCOL_VERSION:
                raise ReconcileError(
                    f"invalid relation contract: protocol_version must be {c.PROTOCOL_VERSION}"
                )
            webhook_url = str(remote_data.get("webhook_url", "")).strip()
            if not webhook_url:
                raise ReconcileError("invalid relation contract: missing webhook_url")

            relation_secret_id = str(remote_data.get("secret_id", "")).strip()
            relation_plaintext_secret = str(remote_data.get("webhook_secret", "")).strip()

            if relation_secret_id:
                relation_secret = self._read_secret_content(relation_secret_id)
                shared_secret = str(relation_secret.get("shared_secret", "")).strip()
                if not shared_secret:
                    raise ReconcileError(
                        "missing required secret field shared_secret in relation secret_id"
                    )
                return WebhookResolution(
                    url=webhook_url,
                    shared_secret=shared_secret,
                    source="relation-secret-id",
                )

            if relation_plaintext_secret:
                return WebhookResolution(
                    url=webhook_url,
                    shared_secret=relation_plaintext_secret,
                    source="relation-plaintext",
                )

            fallback_secret_id = str(config.get("webhook-shared-secret-secret-id", "")).strip()
            if fallback_secret_id:
                fallback_content = self._read_secret_content(fallback_secret_id)
                shared_secret = str(fallback_content.get("shared_secret", "")).strip()
                if not shared_secret:
                    raise ReconcileError(
                        "missing required secret field shared_secret in webhook-shared-secret"
                    )
                return WebhookResolution(
                    url=webhook_url,
                    shared_secret=shared_secret,
                    source="relation-url+config-secret",
                )

            raise ReconcileError(
                "missing webhook secret: relation secret_id, relation webhook_secret, or webhook-shared-secret-secret-id"
            )

        fallback_url = str(config.get("webhook-url", "")).strip()
        if not fallback_url:
            raise ReconcileError("missing webhook URL: relation webhook_url or config webhook-url")

        fallback_secret_id = str(config.get("webhook-shared-secret-secret-id", "")).strip()
        if not fallback_secret_id:
            raise ReconcileError("missing required config: webhook-shared-secret-secret-id")

        fallback_content = self._read_secret_content(fallback_secret_id)
        shared_secret = str(fallback_content.get("shared_secret", "")).strip()
        if not shared_secret:
            raise ReconcileError("missing required secret field shared_secret in webhook-shared-secret")

        return WebhookResolution(url=fallback_url, shared_secret=shared_secret, source="config-fallback")
