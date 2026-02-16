#!/usr/bin/env bash
set -euo pipefail

# Configure local release-filter snap for gcs-release-monitor integration testing.
# Usage:
#   ./scripts/configure-local-release-filter.sh [shared-secret]

SECRET="${1:-gcs-local-integration-secret-20260216}"
CHAINS_FILE="/var/snap/release-filter/current/chains.json"
LOG_FILE="/var/snap/release-filter/current/releases.log"

if ! snap connections release-filter 2>/dev/null | rg -q 'network-bind'; then
  echo "release-filter snap does not expose network-bind."
  echo "Rebuild/reinstall release-filter from /home/jonathan/versioned/dwellir/release-filter with the webhook patch."
  exit 1
fi

sudo install -d -m 755 /var/snap/release-filter/current
printf '[]\n' | sudo tee "${CHAINS_FILE}" >/dev/null

sudo snap set release-filter \
  open-ai-token=disabled \
  poll-interval=3600 \
  chains-file="${CHAINS_FILE}" \
  logging-file="${LOG_FILE}" \
  slack-webhook="" \
  zammad-token="" \
  zammad-url="" \
  zammad-group="" \
  ingest-webhook-enabled=true \
  ingest-webhook-host=0.0.0.0 \
  ingest-webhook-port=8787 \
  ingest-webhook-path=/v1/releases \
  ingest-webhook-secret="${SECRET}" \
  ingest-webhook-max-skew-seconds=300

sudo systemctl reset-failed snap.release-filter.release-filter.service || true
sudo snap start release-filter
sudo snap services release-filter

echo
echo "Configured local release-filter for webhook ingestion."
echo "Health check: curl -sS http://127.0.0.1:8787/healthz"
echo "Log tail: sudo journalctl -u snap.release-filter.release-filter -n 100 --no-pager"
