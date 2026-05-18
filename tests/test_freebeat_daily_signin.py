from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from core.account_graph import load_account_graphs, patch_account_graph
from core.db import AccountModel, engine
from services.freebeat_daily_signin import (
    DEFAULT_SIGN_IN_MAX_INTERVAL_SECONDS,
    DEFAULT_SIGN_IN_MIN_INTERVAL_SECONDS,
    FreebeatDailySignInWorker,
    _dynamic_interval_seconds,
)


def _create_freebeat_account(
    email: str,
    *,
    lifecycle_status: str = "registered",
    valid: bool | None = True,
    with_token: bool = True,
    overview_updates: dict | None = None,
) -> int:
    with Session(engine) as session:
        model = AccountModel(platform="freebeat", email=email, password="")
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

