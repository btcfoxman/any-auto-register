from __future__ import annotations

from sqlmodel import Session

from application.tasks import TASK_STATUS_SUCCEEDED, _execute_account_check_all_task
from core.account_graph import patch_account_graph
from core.db import AccountModel, engine


class _FakeLogger:
    def __init__(self) -> None:
        self.result = None
        self.status = None
        self.progress = []
        self.errors = []
        self.logs = []

    def set_progress(self, completed, total):
        self.progress.append((completed, total))

    def set_result_data(self, data):
        self.result = data

    def finish(self, status, error=None):
        self.status = status
        self.error = error

    def is_cancel_requested(self):
        return False

    def record_error(self, error):
        self.errors.append(error)

    def log(self, message, level="info"):
        self.logs.append((level, message))


def _create_account(email: str, *, lifecycle_status: str = "registered", valid: bool | None = True) -> int:
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


def test_check_all_task_skips_expired_and_invalid_accounts(monkeypatch):
    active_id = _create_account("active@example.com", valid=True)
    invalid_id = _create_account("invalid@example.com", valid=False)
    expired_id = _create_account("expired@example.com", lifecycle_status="expired", valid=True)
    checked_ids = []

    def fake_run_single_account_check(account_id, logger):
        checked_ids.append(account_id)
        return True, {"valid": True}

    monkeypatch.setattr("application.tasks._run_single_account_check", fake_run_single_account_check)

    logger = _FakeLogger()
    _execute_account_check_all_task({"platform": "lingya_qq", "limit": 10}, logger)

    assert checked_ids == [active_id]
    assert invalid_id not in checked_ids
    assert expired_id not in checked_ids
    assert logger.result == {"valid": 1, "invalid": 0, "error": 0, "skipped": 2}
    assert logger.status == TASK_STATUS_SUCCEEDED
