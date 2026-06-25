from __future__ import annotations

from sqlmodel import Session

from core.account_graph import patch_account_graph
from core.db import AccountModel, engine
from services.quickframe_keepalive import DEFAULT_HEARTBEAT_INTERVAL_SECONDS, QuickFrameKeepaliveWorker


def test_quickframe_keepalive_defaults_match_capture_heartbeat_loop():
    assert DEFAULT_HEARTBEAT_INTERVAL_SECONDS == 300


def test_quickframe_keepalive_runs_keepalive_action(monkeypatch):
    calls = []

    class FakeRuntime:
        def execute_action(self, command, log_fn=None):
            calls.append((command.platform, command.account_id, command.action_id, dict(command.params)))
            return type("Result", (), {"ok": True, "data": {}, "error": ""})()

    worker = QuickFrameKeepaliveWorker()
    monkeypatch.setattr("services.quickframe_keepalive.PlatformRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(worker, "_target_account_ids", lambda: [7])

    worker._run_for_accounts()

    assert calls == [
        (
            "quickframe",
            7,
            "keepalive_sync",
            {"force_refresh": "true"},
        )
    ]


def _create_quickframe_account(
    email: str,
    *,
    lifecycle_status: str = "registered",
    valid: bool | None = True,
    overview_updates: dict | None = None,
) -> int:
    with Session(engine) as session:
        model = AccountModel(platform="quickframe", email=email, password="")
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
        )
        session.commit()
        return int(model.id or 0)


def test_quickframe_keepalive_skips_disabled_invalid_and_expired_accounts():
    active_id = _create_quickframe_account("active@example.com", valid=True)
    disabled_id = _create_quickframe_account(
        "disabled@example.com",
        valid=True,
        overview_updates={"quickframe_keepalive_disabled": True},
    )
    invalid_id = _create_quickframe_account("invalid@example.com", valid=False)
    expired_id = _create_quickframe_account("expired@example.com", lifecycle_status="expired", valid=True)

    targets = set(QuickFrameKeepaliveWorker()._target_account_ids())

    assert active_id in targets
    assert disabled_id not in targets
    assert invalid_id not in targets
    assert expired_id not in targets
