from __future__ import annotations

from sqlmodel import Session

from core.account_graph import patch_account_graph
from core.db import AccountModel, engine
from services.lingya_keepalive import (
    DEFAULT_BALANCE_INTERVAL_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    LingYaKeepaliveWorker,
)


def test_lingya_keepalive_defaults_match_lingya2api_lifecycle():
    assert DEFAULT_HEARTBEAT_INTERVAL_SECONDS == 300
    assert DEFAULT_BALANCE_INTERVAL_SECONDS == 60


def test_lingya_keepalive_runs_keepalive_action_with_quota_flag(monkeypatch):
    calls = []

    class FakeRuntime:
        def execute_action(self, command, log_fn=None):
            calls.append((command.platform, command.account_id, command.action_id, dict(command.params)))
            return type("Result", (), {"ok": True, "data": {}, "error": ""})()

    worker = LingYaKeepaliveWorker()
    monkeypatch.setattr("services.lingya_keepalive.PlatformRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(worker, "_target_account_ids", lambda **kwargs: [7])

    worker._run_for_accounts(refresh_quota=False, run_hello=True)
    worker._run_for_accounts(refresh_quota=True, run_hello=False)

    assert calls == [
        (
            "lingya_qq",
            7,
            "keepalive_sync",
            {"force_refresh": "false", "refresh_quota": "false", "run_hello": "true"},
        ),
        (
            "lingya_qq",
            7,
            "keepalive_sync",
            {"force_refresh": "false", "refresh_quota": "true", "run_hello": "false"},
        ),
    ]


def _create_lingya_account(email: str, *, lifecycle_status: str = "registered", valid: bool | None = True) -> int:
    with Session(engine) as session:
        model = AccountModel(platform="lingya_qq", email=email, password="")
        session.add(model)
        session.commit()
        session.refresh(model)
        summary_updates = {} if valid is None else {"valid": valid}
        patch_account_graph(
            session,
            model,
            lifecycle_status=lifecycle_status,
            summary_updates=summary_updates,
        )
        session.commit()
        return int(model.id or 0)


def test_lingya_keepalive_skips_invalid_accounts_when_refreshing_quota():
    active_id = _create_lingya_account("active@example.com", valid=True)
    invalid_id = _create_lingya_account("invalid@example.com", valid=False)
    expired_id = _create_lingya_account("expired@example.com", lifecycle_status="expired", valid=True)

    worker = LingYaKeepaliveWorker()

    assert active_id in worker._target_account_ids(refresh_quota=False)
    assert invalid_id in worker._target_account_ids(refresh_quota=False)
    assert expired_id not in worker._target_account_ids(refresh_quota=False)
    assert set(worker._target_account_ids(refresh_quota=True)) == {active_id}
