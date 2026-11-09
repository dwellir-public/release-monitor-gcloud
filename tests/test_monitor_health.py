import json
import os
from types import SimpleNamespace

import pytest

from gcs_release_monitor.monitor import MonitorService


@pytest.mark.parametrize("error", [RuntimeError, ConnectionError, OSError])
def test_loop_publishes_failure_and_recovers(tmp_path, monkeypatch, error):
    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(poll_interval_seconds=1)
    service.store = SimpleNamespace(bootstrap=lambda: None)
    health_path = tmp_path / "health.json"
    reports = []
    calls = 0

    def poll(dry_run=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert json.loads(health_path.read_text())["status"] == "starting"
            raise error("sensitive credential details")

    def sleep(_interval):
        reports.append(json.loads(health_path.read_text()))
        if len(reports) == 2:
            raise KeyboardInterrupt

    service.run_once = poll
    monkeypatch.setattr("gcs_release_monitor.monitor.time.sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        service.run_forever(health_file=health_path)
    assert [r["status"] for r in reports] == ["failed", "ok"]
    assert reports[0]["error_type"] == error.__name__
    assert reports[1]["error_type"] == ""
    assert reports[0]["pid"] == os.getpid()
    assert "sensitive" not in json.dumps(reports)
    assert not health_path.with_suffix(".new").exists()
