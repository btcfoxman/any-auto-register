from __future__ import annotations

from services.lingya_keepalive import (
    DEFAULT_BALANCE_INTERVAL_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    LingYaKeepaliveWorker,
)


def test_lingya_keepalive_defaults_match_lingya2api_lifecycle():
    assert DEFAULT_HEARTBEAT_INTERVAL_SECONDS == 300
    assert DEFAULT_BALANCE_INTERVAL_SECONDS == 600


def test_lingya_keepalive_runs_keepalive_action_with_quota_flag(monkeypatch):
    calls = []

    class FakeRuntime:
        def execute_action(self, command, log_fn=None):
            calls.append((command.platform, command.account_id, command.action_id, dict(command.params)))
            return type("Result", (), {"ok": True, "data": {}, "error": ""})()

    worker = LingYaKeepaliveWorker()
    monkeypatch.setattr("services.lingya_keepalive.PlatformRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(worker, "_target_account_ids", lambda: [7])

    worker._run_for_accounts(refresh_quota=False)
    worker._run_for_accounts(refresh_quota=True)

    assert calls == [
        ("lingya_qq", 7, "keepalive_sync", {"refresh_quota": "false"}),
        ("lingya_qq", 7, "keepalive_sync", {"refresh_quota": "true"}),
    ]
