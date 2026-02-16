#!/usr/bin/env bash
set -euo pipefail

# Configure local release-filter snap for gcs-release-monitor integration testing.
# Usage:
#   ./scripts/configure-local-release-filter.sh [shared-secret]
#
# Behavior:
# - Preserves existing snap values when present.
# - Uses defaults only when a value is missing.
# - Forces webhook ingestion enabled for local integration testing.

DEFAULT_SECRET="gcs-local-integration-secret-20260216"
DEFAULT_CHAINS_FILE="/var/snap/release-filter/current/chains.json"
DEFAULT_LOG_FILE="/var/snap/release-filter/current/releases.log"
CONFIG_JSON="/var/snap/release-filter/current/config.json"

if ! snap connections release-filter 2>/dev/null | rg -q "network-bind"; then
  echo "release-filter snap does not expose network-bind."
  echo "Rebuild/reinstall release-filter from /home/jonathan/versioned/dwellir/release-filter with the webhook patch."
  exit 1
fi

get_current_config_value() {
  local key="$1"
  local fallback="$2"

  python3 - "$CONFIG_JSON" "$key" "$fallback" <<PYCFG
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
fallback = sys.argv[3]

if not path.exists():
    print(fallback)
    raise SystemExit

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print(fallback)
    raise SystemExit

value = data.get(key)
if value is None:
    print(fallback)
    raise SystemExit

if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, float):
    print(str(int(value)) if value.is_integer() else str(value))
elif isinstance(value, int):
    print(str(value))
else:
    text = str(value).strip()
    print(text if text else fallback)
PYCFG
}

if [[ $# -gt 0 ]]; then
  SECRET="$1"
else
  SECRET="$(get_current_config_value ingest_webhook_secret "$DEFAULT_SECRET")"
fi

OPEN_AI_TOKEN="$(get_current_config_value token "disabled")"
POLL_INTERVAL="$(get_current_config_value poll_interval "3600")"
CHAINS_FILE="$(get_current_config_value chains "$DEFAULT_CHAINS_FILE")"
LOG_FILE="$(get_current_config_value logging_file "$DEFAULT_LOG_FILE")"
SLACK_WEBHOOK="$(get_current_config_value webhook "")"
ZAMMAD_TOKEN="$(get_current_config_value zammad_token "")"
ZAMMAD_URL="$(get_current_config_value zammad_url "")"
ZAMMAD_GROUP="$(get_current_config_value zammad_group "")"
INGEST_HOST="$(get_current_config_value ingest_webhook_host "0.0.0.0")"
INGEST_PORT="$(get_current_config_value ingest_webhook_port "8787")"
INGEST_PATH="$(get_current_config_value ingest_webhook_path "/v1/releases")"
INGEST_MAX_SKEW="$(get_current_config_value ingest_webhook_max_skew_seconds "300")"

sudo install -d -m 755 "$(dirname "$CHAINS_FILE")"
if ! sudo test -f "$CHAINS_FILE"; then
  echo "[]" | sudo tee "$CHAINS_FILE" >/dev/null
fi

sudo snap set release-filter \
  open-ai-token="$OPEN_AI_TOKEN" \
  poll-interval="$POLL_INTERVAL" \
  chains-file="$CHAINS_FILE" \
  logging-file="$LOG_FILE" \
  slack-webhook="$SLACK_WEBHOOK" \
  zammad-token="$ZAMMAD_TOKEN" \
  zammad-url="$ZAMMAD_URL" \
  zammad-group="$ZAMMAD_GROUP" \
  ingest-webhook-enabled=true \
  ingest-webhook-host="$INGEST_HOST" \
  ingest-webhook-port="$INGEST_PORT" \
  ingest-webhook-path="$INGEST_PATH" \
  ingest-webhook-secret="$SECRET" \
  ingest-webhook-max-skew-seconds="$INGEST_MAX_SKEW"

sudo systemctl reset-failed snap.release-filter.release-filter.service || true
sudo snap start release-filter
sudo snap services release-filter

echo
echo "Configured local release-filter for webhook ingestion."
echo "Preserved open-ai-token value: ${OPEN_AI_TOKEN}"
echo "Using chains file: ${CHAINS_FILE}"
echo "Health check: curl -sS http://127.0.0.1:${INGEST_PORT}/healthz"
echo "Log tail: sudo journalctl -u snap.release-filter.release-filter -n 100 --no-pager"
