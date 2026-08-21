from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from core.account_graph import load_account_graphs, patch_account_graph
from core.db import AccountModel, engine
from infrastructure.config_repository import ConfigRepository
from services.freebeat_daily_signin import (
    DEFAULT_RETIRE_CREDIT_THRESHOLD,
    DEFAULT_SIGN_IN_MAX_INTERVAL_SECONDS,
    DEFAULT_SIGN_IN_MIN_INTERVAL_SECONDS,
    FreebeatDailySignInWorker,
    _dynamic_interval_seconds,
    _retire_settings,
)


def _create_freebeat_account(
    email: str,
    *,
    lifecycle_status: str = "registered",
    valid: bool | None = True,
    with_token: bool = True,
    created_at: datetime | None = None,
    overview_updates: dict | None = None,
) -> int:
    with Session(engine) as session:
        model = AccountModel(platform="freebeat", email=email, password="")
        if created_at is not None:
            model.created_at = created_at
        session.add(model)
        session.commit()
        session.refresh(model)
        summary_updates = {} if valid is None else {"valid": valid}
        if overview_updates:
            summary_updates.update(overview_updates)
        patch_account_graph(
            session,
            model,
            lifecycle_status=lifecycle_status,
            summary_updates=summary_updates,
            credential_updates={"access_token": f"token-{model.id}"} if with_token else None,
        )
        session.commit()
        return int(model.id or 0)


def test_freebeat_daily_signin_dynamic_interval_uses_configured_range(monkeypatch):
    captured: dict[str, int] = {}

    def fake_randint(minimum: int, maximum: int) -> int:
        captured["minimum"] = minimum
        captured["maximum"] = maximum
        return maximum

    monkeypatch.setattr("services.freebeat_daily_signin.random.randint", fake_randint)

    interval = _dynamic_interval_seconds(
        {
            "freebeat_daily_sign_in_min_interval_seconds": "120",
            "freebeat_daily_sign_in_max_interval_seconds": "300",
        }
    )

    assert interval == 300
    assert captured == {"minimum": 120, "maximum": 300}


def test_freebeat_daily_signin_dynamic_interval_defaults(monkeypatch):
    captured: dict[str, int] = {}

    def fake_randint(minimum: int, maximum: int) -> int:
        captured["minimum"] = minimum
        captured["maximum"] = maximum
        return minimum

    monkeypatch.setattr("services.freebeat_daily_signin.random.randint", fake_randint)

    interval = _dynamic_interval_seconds({})

    assert interval == DEFAULT_SIGN_IN_MIN_INTERVAL_SECONDS
    assert captured == {
        "minimum": DEFAULT_SIGN_IN_MIN_INTERVAL_SECONDS,
        "maximum": DEFAULT_SIGN_IN_MAX_INTERVAL_SECONDS,
    }


def test_freebeat_daily_signin_retire_settings_default_and_override():
    assert _retire_settings({}) == (True, DEFAULT_RETIRE_CREDIT_THRESHOLD, 24)
    assert _retire_settings({"freebeat_retire_credit_threshold": "300"}) == (True, 300, 24)


def test_freebeat_retire_settings_are_configurable():
    allowed = ConfigRepository().get_allowed_keys()

    assert {
        "freebeat_retire_low_credit_enabled",
        "freebeat_retire_credit_threshold",
        "freebeat_retire_after_hours",
    }.issubset(allowed)


def test_freebeat_mail_provider_is_configurable():
    assert "freebeat_mail_provider" in ConfigRepository().get_allowed_keys()


def test_freebeat_daily_signin_targets_only_due_active_accounts():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    eligible_id = _create_freebeat_account("eligible@example.com")
    _create_freebeat_account("invalid@example.com", valid=False)
    _create_freebeat_account("expired@example.com", lifecycle_status="expired")
    _create_freebeat_account("no-token@example.com", with_token=False)
    _create_freebeat_account(
        "disabled@example.com",
        overview_updates={"freebeat_daily_sign_in_disabled": True},
    )
    _create_freebeat_account(
        "already-signed@example.com",
        overview_updates={"signed_today": True, "next_refresh_at": now_ms + 3600_000},
    )
    due_again_id = _create_freebeat_account(
        "due-again@example.com",
        overview_updates={"signed_today": True, "next_refresh_at": now_ms - 60_000},
    )

    targets = set(FreebeatDailySignInWorker()._target_account_ids())

    assert eligible_id in targets
    assert due_again_id in targets
    assert len(targets) == 2


def test_freebeat_daily_signin_runs_daily_sign_action(monkeypatch):
    calls: list[tuple[str, int, str]] = []

    class FakeRuntime:
        def execute_action(self, command, log_fn=None):
            calls.append((command.platform, command.account_id, command.action_id))
            return type(
                "Result",
                (),
                {"ok": True, "data": {"daily_sign_in_status": "signed", "total_credits": 1000}, "error": ""},
            )()

    worker = FreebeatDailySignInWorker()
    monkeypatch.setattr("services.freebeat_daily_signin.PlatformRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(worker, "_target_account_ids", lambda: [7, 8])

    worker._run_for_accounts()

    assert calls == [
        ("freebeat", 7, "daily_sign_in"),
        ("freebeat", 8, "daily_sign_in"),
    ]


def test_freebeat_daily_signin_skips_signed_until_future_refresh():
    future_ms = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp() * 1000)
    account_id = _create_freebeat_account(
        "future@example.com",
        overview_updates={"signed_today": True, "next_refresh_at": future_ms},
    )

    assert account_id not in set(FreebeatDailySignInWorker()._target_account_ids())


def test_freebeat_daily_signin_retires_old_low_credit_accounts(monkeypatch):
    old_created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    low_id = _create_freebeat_account(
        "old-low-credit@example.com",
        created_at=old_created_at,
        overview_updates={"total_credits": 0},
    )
    fresh_id = _create_freebeat_account(
        "fresh-low-credit@example.com",
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        overview_updates={"total_credits": 0},
    )
    enough_id = _create_freebeat_account(
        "old-enough-credit@example.com",
        created_at=old_created_at,
        overview_updates={"total_credits": 1},
    )

    worker = FreebeatDailySignInWorker()
    monkeypatch.setattr(worker, "_config", lambda: {
        "freebeat_retire_low_credit_enabled": "true",
        "freebeat_retire_after_hours": "24",
    })
    monkeypatch.setattr(worker, "_sync_retired_remote_auto_maintenance", lambda account_id: None)

    targets = set(worker._target_account_ids())

    assert low_id not in targets
    assert fresh_id in targets
    assert enough_id in targets

    with Session(engine) as session:
        graph = load_account_graphs(session, [low_id])[low_id]
        overview = graph["overview"]

    assert graph["lifecycle_status"] == "expired"
    assert overview["freebeat_retired"] is True
    assert overview["freebeat_retire_reason"] == "low_credits_after_age"
    assert overview["freebeat_retire_credit_balance"] == 0
    assert overview["freebeat_retire_credit_threshold"] == 1
    assert overview["freebeat_retire_after_hours"] == 24
    assert overview["freebeat_daily_sign_in_disabled"] is True
    assert overview["freebeat_keepalive_disabled"] is True
    assert overview["freebeat2api_enable_auto_maintenance"] is False
