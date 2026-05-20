from __future__ import annotations

from core.base_platform import Account, AccountStatus
from application import tasks


class _Logger:
    def __init__(self):
        self.messages: list[str] = []
        self.finished = ""

    def log(self, message: str, **kwargs):
        self.messages.append(message)

    def set_progress(self, current: int, total: int | None = None):
        return None

    def is_cancel_requested(self) -> bool:
        return False

    def record_success(self):
        return None

    def record_error(self, error: str):
        self.messages.append(error)

    def add_cashier_url(self, url: str):
        return None

    def set_result_data(self, data):
        return None

    def finish(self, status: str, *, error: str = ""):
        self.finished = status


def _patch_register_task_common(monkeypatch, saved: list[Account], resolved: list[str | None]):
    class FakePlatform:
        def register(self, email=None, password=None):
            return Account(
                platform="lingya_qq",
                email="+8613800138000",
                password="",
                user_id="vuid",
                token="session",
                status=AccountStatus.REGISTERED,
                extra={"cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device"},
            )

    monkeypatch.setattr(tasks, "get", lambda platform_name: object())
    monkeypatch.setattr(tasks, "_resolve_sms_provider_for_task", lambda extra: ("", {}))
    monkeypatch.setattr(tasks, "save_account", lambda account: saved.append(account))
    monkeypatch.setattr(tasks, "_auto_followup_windsurf_payment", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "_auto_followup_lingya_qq_rewards", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "_auto_upload_cpa", lambda logger, account: None)
    monkeypatch.setattr(tasks, "_auto_push_any2api", lambda logger, account: None)
    monkeypatch.setattr(tasks, "_auto_sync_lingya2api", lambda logger, account: None)

    def fake_build_platform_instance(platform_name, payload, logger, resolved_proxy=None, shared_mailbox=None):
        resolved.append(resolved_proxy)
        return FakePlatform()

    monkeypatch.setattr(tasks, "_build_platform_instance", fake_build_platform_instance)


def test_register_task_does_not_use_proxy_pool_by_default(monkeypatch):
    saved: list[Account] = []
    resolved: list[str | None] = []
    _patch_register_task_common(monkeypatch, saved, resolved)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.get_next", lambda region="": (_ for _ in ()).throw(AssertionError("proxy pool called")))

    logger = _Logger()
    tasks._execute_register_task(
        {
            "platform": "lingya_qq",
            "count": 1,
            "concurrency": 1,
            "executor_type": "manual_assisted",
            "extra": {"identity_provider": "manual_phone"},
        },
        logger,
    )

    assert logger.finished == tasks.TASK_STATUS_SUCCEEDED
    assert resolved == [None]
    assert saved[0].extra.get("proxy_url") in (None, "")


def test_register_task_can_use_proxy_pool_and_persists_resolved_proxy(monkeypatch):
    saved: list[Account] = []
    resolved: list[str | None] = []
    events: list[tuple[str, str]] = []
    _patch_register_task_common(monkeypatch, saved, resolved)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.get_next", lambda region="": "http://user:pass@1.2.3.4:8080")
    monkeypatch.setattr("core.proxy_pool.proxy_pool.report_success", lambda url: events.append(("success", url)))
    monkeypatch.setattr("core.proxy_pool.proxy_pool.report_fail", lambda url: events.append(("fail", url)))

    logger = _Logger()
    tasks._execute_register_task(
        {
            "platform": "lingya_qq",
            "count": 1,
            "concurrency": 1,
            "executor_type": "manual_assisted",
            "use_proxy_pool": True,
            "extra": {"identity_provider": "manual_phone"},
        },
        logger,
    )

    assert logger.finished == tasks.TASK_STATUS_SUCCEEDED
    assert resolved == ["http://user:pass@1.2.3.4:8080"]
    assert saved[0].extra["proxy_url"] == "http://user:pass@1.2.3.4:8080"
    assert ("success", "http://user:pass@1.2.3.4:8080") in events


def test_freebeat_register_falls_back_direct_after_proxy_network_failure(monkeypatch):
    saved: list[Account] = []
    resolved: list[str | None] = []
    events: list[tuple[str, str]] = []

    class FakePlatform:
        def register(self, email=None, password=None):
            return Account(
                platform="freebeat",
                email="user@example.com",
                password="",
                user_id="user_123",
                token="tok_123",
                status=AccountStatus.REGISTERED,
                extra={"access_token": "tok_123"},
            )

    monkeypatch.setattr(tasks, "get", lambda platform_name: object())
    monkeypatch.setattr(tasks, "_resolve_sms_provider_for_task", lambda extra: ("", {}))
    monkeypatch.setattr(tasks, "save_account", lambda account: saved.append(account))
    monkeypatch.setattr(tasks, "_auto_followup_windsurf_payment", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "_auto_followup_lingya_qq_rewards", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "_auto_upload_cpa", lambda logger, account: None)
    monkeypatch.setattr(tasks, "_auto_push_any2api", lambda logger, account: None)
    monkeypatch.setattr(tasks, "_auto_sync_lingya2api", lambda logger, account: None)
    monkeypatch.setattr(tasks, "_auto_sync_freebeat2api", lambda logger, account: None)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.get_next", lambda region="": "socks5://xray:20005")
    monkeypatch.setattr("core.proxy_pool.proxy_pool.report_success", lambda url: events.append(("success", url)))
    monkeypatch.setattr("core.proxy_pool.proxy_pool.report_fail", lambda url: events.append(("fail", url)))

    def fake_preflight(platform_name, proxy, logger):
        if proxy:
            raise RuntimeError("Failed to perform, curl: (28) Connection timed out after 8000 milliseconds")

    def fake_build_platform_instance(platform_name, payload, logger, resolved_proxy=None, shared_mailbox=None):
        resolved.append(resolved_proxy)
        return FakePlatform()

    monkeypatch.setattr(tasks, "_preflight_platform_proxy", fake_preflight)
    monkeypatch.setattr(tasks, "_build_platform_instance", fake_build_platform_instance)

    logger = _Logger()
    tasks._execute_register_task(
        {
            "platform": "freebeat",
            "count": 1,
            "concurrency": 1,
            "executor_type": "protocol",
            "use_proxy_pool": True,
            "extra": {"identity_provider": "manual_phone"},
        },
        logger,
    )

    assert logger.finished == tasks.TASK_STATUS_SUCCEEDED
    assert resolved == [None]
    assert saved[0].extra.get("proxy_url") in (None, "")
    assert ("fail", "socks5://xray:20005") in events
    assert not any(event[0] == "success" for event in events)
