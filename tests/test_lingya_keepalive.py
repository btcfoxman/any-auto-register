from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from core.account_graph import load_account_graphs, patch_account_graph
from core.db import AccountModel, engine
from domain.actions import ActionExecutionCommand
from infrastructure.platform_runtime import PlatformRuntime
from services.lingya_keepalive import (
    DEFAULT_BALANCE_INTERVAL_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_RETIRE_AFTER_HOURS,
    DEFAULT_RETIRE_QUOTA_THRESHOLD,
    LingYaKeepaliveWorker,
)


def test_lingya_keepalive_defaults_match_lingya2api_lifecycle():
    assert DEFAULT_HEARTBEAT_INTERVAL_SECONDS == 300
    assert DEFAULT_BALANCE_INTERVAL_SECONDS == 60
    assert DEFAULT_RETIRE_QUOTA_THRESHOLD == 57
    assert DEFAULT_RETIRE_AFTER_HOURS == 24


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


def _create_lingya_account(
    email: str,
    *,
    lifecycle_status: str = "registered",
    valid: bool | None = True,
    quota_balance: int | str | None = None,
    created_at: datetime | None = None,
    overview_updates: dict | None = None,
) -> int:
    with Session(engine) as session:
        model = AccountModel(platform="lingya_qq", email=email, password="")
        if created_at is not None:
            model.created_at = created_at
            model.updated_at = created_at
        session.add(model)
        session.commit()
        session.refresh(model)
        summary_updates = {} if valid is None else {"valid": valid}
        if quota_balance is not None:
            summary_updates["quota_balance"] = quota_balance
        if overview_updates:
            summary_updates.update(overview_updates)
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


def test_lingya_keepalive_retires_old_low_quota_accounts(monkeypatch):
    old_created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    low_id = _create_lingya_account(
        "old-low-quota@example.com",
        quota_balance=56,
        created_at=old_created_at,
    )
    fresh_id = _create_lingya_account(
        "fresh-low-quota@example.com",
        quota_balance=56,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    enough_id = _create_lingya_account(
        "old-enough-quota@example.com",
        quota_balance=57,
        created_at=old_created_at,
    )

    worker = LingYaKeepaliveWorker()
    monkeypatch.setattr(worker, "_config", lambda: {
        "lingya_qq_keepalive_retire_enabled": "true",
        "lingya_qq_keepalive_retire_quota_threshold": "57",
        "lingya_qq_keepalive_retire_after_hours": "24",
    })

    targets = set(worker._target_account_ids(refresh_quota=False))

    assert low_id not in targets
    assert fresh_id in targets
    assert enough_id in targets

    with Session(engine) as session:
        graph = load_account_graphs(session, [low_id])[low_id]
        overview = graph["overview"]

    assert graph["lifecycle_status"] == "expired"
    assert overview["lingya_keepalive_retired"] is True
    assert overview["lingya_keepalive_retire_reason"] == "low_quota_after_age"
    assert overview["lingya_keepalive_retire_quota_balance"] == 56
    assert overview["lingya_keepalive_retire_quota_threshold"] == 57
    assert overview["lingya_keepalive_retire_after_hours"] == 24


def test_lingya_keepalive_skips_manually_disabled_accounts():
    disabled_id = _create_lingya_account(
        "manual-disabled@example.com",
        overview_updates={"lingya_keepalive_disabled": True},
    )
    active_id = _create_lingya_account("manual-active@example.com")

    targets = set(LingYaKeepaliveWorker()._target_account_ids(refresh_quota=False))

    assert disabled_id not in targets
    assert active_id in targets

    with Session(engine) as session:
        graph = load_account_graphs(session, [disabled_id])[disabled_id]

    assert graph["lifecycle_status"] == "registered"
    assert graph["overview"]["lingya_keepalive_disabled"] is True


def test_lingya_keepalive_stop_and_resume_actions_persist_account_marker():
    account_id = _create_lingya_account("manual-action@example.com")
    runtime = PlatformRuntime()

    stop_result = runtime.execute_action(
        ActionExecutionCommand(
            platform="lingya_qq",
            account_id=account_id,
            action_id="stop_keepalive",
            params={"reason": "manual"},
        )
    )

    assert stop_result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    assert graph["overview"]["lingya_keepalive_disabled"] is True
    assert account_id not in LingYaKeepaliveWorker()._target_account_ids(refresh_quota=False)

    resume_result = runtime.execute_action(
        ActionExecutionCommand(
            platform="lingya_qq",
            account_id=account_id,
            action_id="resume_keepalive",
            params={},
        )
    )

    assert resume_result.ok is True
    with Session(engine) as session:
        graph = load_account_graphs(session, [account_id])[account_id]
    assert graph["overview"]["lingya_keepalive_disabled"] is False
    assert account_id in LingYaKeepaliveWorker()._target_account_ids(refresh_quota=False)
