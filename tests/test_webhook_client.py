from gcs_release_monitor.webhook_client import build_signed_payload


def test_signed_payload_is_deterministic_for_fixed_timestamp() -> None:
    payload = {"a": 1, "b": "x"}
    signed = build_signed_payload(payload, secret="s3cr3t", timestamp=1700000000)

    assert signed.timestamp == "1700000000"
    assert signed.signature == "sha256=9072467d5ceb5bc0d98398aa6d471a054a25d75b0f65cf3583ed9f06038ec509"
    assert signed.body == b'{"a":1,"b":"x"}'
