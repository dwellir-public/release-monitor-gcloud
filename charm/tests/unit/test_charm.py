from pathlib import Path

import pytest
import yaml

from charm import (
    APP_DIR,
    CONFIG_PATH,
    ReconcileError,
    SecretBundle,
    WebhookResolution,
    _build_render_config,
    parse_json_array_option,
    render_service_unit,
)


def test_parse_json_array_option_accepts_array():
    assert parse_json_array_option('["a", "b"]', 'x') == ['a', 'b']


def test_parse_json_array_option_rejects_non_array():
    with pytest.raises(ReconcileError, match='must be a JSON array'):
        parse_json_array_option('{"k": 1}', 'x')


def test_build_render_config_maps_charm_keys_to_app_schema():
    cfg = {
        'poll-interval-seconds': 123,
        'state-dir': '/var/lib/release-monitor-gcloud/state',
        'temp-dir': '/var/lib/release-monitor-gcloud/tmp',
        'gcs-bucket': 'bucket-a',
        'gcs-include-prefixes': '["rpc/"]',
        'gcs-include-suffixes': '[".tar.gz"]',
        'gcs-include-content-types': '["application/gzip"]',
        'nextcloud-base-url': 'https://cloud.example',
        'nextcloud-remote-dir': 'releases',
        'chain-organization': 'org',
        'chain-repository': 'repo',
        'chain-ids': '[]',
        'chain-genesis-hashes': '[]',
        'artifact-selection-default-binary-patterns': '["rpc-node-*"]',
        'artifact-selection-default-genesis-patterns': '["mainnet/genesis.json"]',
        'artifact-selection-rules': '[]',
    }
    secrets = SecretBundle(
        nextcloud_username='u',
        nextcloud_app_password='p',
        nextcloud_share_password=None,
        gcs_service_account_json=None,
        webhook_shared_secret='secret',
    )
    webhook = WebhookResolution(url='https://hook', shared_secret='secret', source='relation')

    rendered = _build_render_config(cfg, secrets, webhook, gcs_credentials_file=None)

    assert rendered['poll_interval_seconds'] == 123
    assert rendered['gcs']['bucket'] == 'bucket-a'
    assert rendered['nextcloud']['base_url'] == 'https://cloud.example'
    assert rendered['chain']['organization'] == 'org'
    assert rendered['release_defaults']['due_date'] == 'P2D'
    assert rendered['artifact_selection']['default_binary_patterns'] == ['rpc-node-*']


def test_render_service_unit_contains_expected_exec_start():
    unit_text = render_service_unit(log_level='INFO')
    assert str(CONFIG_PATH) in unit_text
    assert '--log-level INFO' in unit_text
    assert 'User=release-monitor' in unit_text


def test_yaml_dump_round_trip():
    sample = {'state_dir': str(APP_DIR / 'state'), 'poll_interval_seconds': 900}
    dumped = yaml.safe_dump(sample)
    assert yaml.safe_load(dumped)['poll_interval_seconds'] == 900
