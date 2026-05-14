from __future__ import annotations

from types import SimpleNamespace

from application import tasks


class _Logger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def log(self, message: str, level: str = "info") -> None:
        self.entries.append((message, level))

    def is_cancel_requested(self) -> bool:
        return False


def test_lingya_followup_is_queued_in_background(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            started.append(self)

    monkeypatch.setattr(tasks.threading, "Thread", FakeThread)
    logger = _Logger()
    account = SimpleNamespace(platform="lingya_qq", email="user@example.com")
    platform = SimpleNamespace(execute_action=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not run")))

    tasks._auto_followup_lingya_qq_rewards(
        platform_name="lingya_qq",
        payload={"extra": {"lingya_qq_auto_daily_sign_in": "false"}},
        platform=platform,
        account=account,
        logger=logger,
    )

    assert len(started) == 1
    assert started[0].daemon is True
    assert "lingya-followup-user@example.com" in started[0].name
    assert any("queued in background" in message for message, _ in logger.entries)


def test_lingya_followup_background_target_logs_required_publish_errors(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            started.append(self)

    monkeypatch.setattr(tasks.threading, "Thread", FakeThread)
    logger = _Logger()
    account = SimpleNamespace(platform="lingya_qq", email="user@example.com")
    platform = SimpleNamespace(execute_action=lambda *args, **kwargs: {"ok": True, "data": {}})

    tasks._auto_followup_lingya_qq_rewards(
        platform_name="lingya_qq",
        payload={
            "extra": {
                "lingya_qq_auto_daily_sign_in": "false",
                "lingya_qq_auto_publish_after_register": "true",
                "lingya_qq_publish_required": "true",
            }
        },
        platform=platform,
        account=account,
        logger=logger,
    )

    started[0].target()

    assert any(level == "warning" and "async follow-up error" in message for message, level in logger.entries)


def test_lingya_followup_skips_daily_sign_when_disabled():
    calls = []
    logger = _Logger()
    account = SimpleNamespace(platform="lingya_qq", email="user@example.com", extra={})
    platform = SimpleNamespace(execute_action=lambda *args, **kwargs: calls.append(args) or {"ok": True, "data": {}})

    tasks._run_auto_followup_lingya_qq_rewards(
        platform_name="lingya_qq",
        payload={
            "extra": {
                "lingya_qq_daily_sign_in_enabled": "false",
                "lingya_qq_auto_publish_after_register": "false",
            }
        },
        platform=platform,
        account=account,
        logger=logger,
    )

    assert calls == []
    assert any("签到功能已关闭" in message for message, _ in logger.entries)


def test_lingya_followup_syncs_lingya2api_after_publish_success(monkeypatch):
    saved_accounts = []
    synced_accounts = []
    logger = _Logger()
    account = SimpleNamespace(platform="lingya_qq", email="user@example.com", extra={})

    def fake_execute_action(action_id, action_account, params):
        assert action_id == "publish_work"
        assert action_account is account
        return {
            "ok": True,
            "data": {
                "last_publish_status": "released",
                "last_publish_vid": "vid123",
                "quota_balance": 9,
                "quota_sum": 10,
                "lingya_qq_publish_source_url": "https://example.com/work",
            },
        }

    monkeypatch.setattr(tasks, "save_account", lambda item: saved_accounts.append(item))
    monkeypatch.setattr(tasks, "_auto_sync_lingya2api", lambda _logger, item: synced_accounts.append(item))

    tasks._run_auto_followup_lingya_qq_rewards(
        platform_name="lingya_qq",
        payload={
            "extra": {
                "lingya_qq_auto_daily_sign_in": "false",
                "lingya_qq_auto_publish_after_register": "true",
                "lingya_qq_publish_source_url": "https://example.com/work",
            }
        },
        platform=SimpleNamespace(execute_action=fake_execute_action),
        account=account,
        logger=logger,
    )

    assert saved_accounts == [account]
    assert synced_accounts == [account]
    assert account.extra["last_publish_status"] == "released"
    assert account.extra["account_overview"]["last_publish_vid"] == "vid123"
    assert account.extra["account_overview"]["quota_balance"] == 9
    assert any("发布完成后再次同步" in message for message, _ in logger.entries)
