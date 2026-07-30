from __future__ import annotations

import threading

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


def test_register_task_preserves_platform_actual_proxy(monkeypatch):
    saved: list[Account] = []
    resolved: list[str | None] = []
    _patch_register_task_common(monkeypatch, saved, resolved)
    monkeypatch.setattr(
        "core.proxy_pool.proxy_pool.get_next",
        lambda region="": "socks5://127.0.0.1:20001",
    )

    class FakePlatform:
        def register(self, email=None, password=None):
            return Account(
                platform="higg",
                email="higg@example.com",
                password="",
                status=AccountStatus.REGISTERED,
                extra={"proxy_url": "socks5://127.0.0.1:20013"},
            )

    monkeypatch.setattr(
        tasks,
        "_build_platform_instance",
        lambda *args, **kwargs: FakePlatform(),
    )

    logger = _Logger()
    tasks._execute_register_task(
        {
            "platform": "higg",
            "count": 1,
            "concurrency": 1,
            "executor_type": "protocol",
            "use_proxy_pool": True,
            "extra": {"identity_provider": "manual_phone"},
        },
        logger,
    )

    assert logger.finished == tasks.TASK_STATUS_SUCCEEDED
    assert saved[0].extra["proxy_url"] == "socks5://127.0.0.1:20013"


def test_register_task_fails_when_selected_proxy_pool_is_empty(monkeypatch):
    saved: list[Account] = []
    resolved: list[str | None] = []
    _patch_register_task_common(monkeypatch, saved, resolved)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.get_next", lambda region="": None)

    logger = _Logger()
    tasks._execute_register_task(
        {
            "platform": "higg",
            "count": 1,
            "concurrency": 1,
            "executor_type": "protocol",
            "use_proxy_pool": True,
            "extra": {"identity_provider": "manual_phone"},
        },
        logger,
    )

    assert logger.finished == tasks.TASK_STATUS_FAILED
    assert resolved == []
    assert saved == []
    assert any("没有获取到可用代理" in message for message in logger.messages)


def test_register_task_normalizes_socks_proxy_alias(monkeypatch):
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
            "proxy": "socks://127.0.0.1:20003",
            "extra": {"identity_provider": "manual_phone"},
        },
        logger,
    )

    assert logger.finished == tasks.TASK_STATUS_SUCCEEDED
    assert resolved == ["socks5://127.0.0.1:20003"]
    assert saved[0].extra["proxy_url"] == "socks5://127.0.0.1:20003"


def test_register_task_uses_primary_proxy_candidate_lazily(monkeypatch):
    saved: list[Account] = []
    resolved: list[str | None] = []
    _patch_register_task_common(monkeypatch, saved, resolved)

    proxies = iter([
        "http://proxy-1:8080",
        "http://proxy-2:8080",
        "http://proxy-3:8080",
        "http://proxy-4:8080",
        "http://proxy-5:8080",
    ])
    calls: list[str] = []

    def fake_get_next(region: str = ""):
        proxy = next(proxies)
        calls.append(proxy)
        return proxy

    monkeypatch.setattr("core.proxy_pool.proxy_pool.get_next", fake_get_next)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.report_success", lambda url: None)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.report_fail", lambda url: None)

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
    assert calls == ["http://proxy-1:8080"]
    assert resolved == ["http://proxy-1:8080"]


def test_register_task_balances_proxy_pool_across_parallel_workers(monkeypatch):
    saved: list[Account] = []
    resolved: list[str | None] = []
    barrier = threading.Barrier(4)
    email_lock = threading.Lock()
    email_index = {"value": 0}

    class FakePlatform:
        def __init__(self, resolved_proxy: str | None):
            self.resolved_proxy = resolved_proxy

        def register(self, email=None, password=None):
            barrier.wait(timeout=5)
            with email_lock:
                email_index["value"] += 1
                suffix = email_index["value"]
            return Account(
                platform="lingya_qq",
                email=f"+86138001380{suffix:02d}",
                password="",
                user_id=f"vuid-{suffix}",
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
    monkeypatch.setattr(tasks, "_existing_account_id", lambda platform, email: 0)

    def fake_build_platform_instance(platform_name, payload, logger, resolved_proxy=None, shared_mailbox=None):
        resolved.append(resolved_proxy)
        return FakePlatform(resolved_proxy)

    monkeypatch.setattr(tasks, "_build_platform_instance", fake_build_platform_instance)

    proxies = [
        "http://proxy-1:8080",
        "http://proxy-2:8080",
        "http://proxy-3:8080",
        "http://proxy-4:8080",
    ]
    cursor = {"value": 0}
    cursor_lock = threading.Lock()

    def fake_get_next(region: str = ""):
        with cursor_lock:
            proxy = proxies[cursor["value"] % len(proxies)]
            cursor["value"] += 1
        return proxy

    monkeypatch.setattr("core.proxy_pool.proxy_pool.get_next", fake_get_next)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.report_success", lambda url: None)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.report_fail", lambda url: None)

    logger = _Logger()
    tasks._execute_register_task(
        {
            "platform": "lingya_qq",
            "count": 4,
            "concurrency": 4,
            "executor_type": "manual_assisted",
            "use_proxy_pool": True,
            "extra": {"identity_provider": "manual_phone"},
        },
        logger,
    )

    assert logger.finished == tasks.TASK_STATUS_SUCCEEDED
    assert len(saved) == 4
    assert set(resolved) == set(proxies)


def test_higg_register_switches_proxy_after_captcha_risk(monkeypatch):
    saved: list[Account] = []
    resolved: list[str | None] = []
    events: list[tuple[str, str]] = []
    _patch_register_task_common(monkeypatch, saved, resolved)

    proxies = iter(
        [
            "socks5://xray:20101",
            "socks5://xray:20102",
        ]
    )
    monkeypatch.setattr(
        "core.proxy_pool.proxy_pool.get_next",
        lambda region="": next(proxies),
    )
    monkeypatch.setattr(
        "core.proxy_pool.proxy_pool.report_success",
        lambda url: events.append(("success", url)),
    )
    monkeypatch.setattr(
        "core.proxy_pool.proxy_pool.report_fail",
        lambda url: events.append(("fail", url)),
    )

    class FakePlatform:
        def __init__(self, resolved_proxy: str | None):
            self.resolved_proxy = resolved_proxy

        def register(self, email=None, password=None):
            if self.resolved_proxy == "socks5://xray:20101":
                raise RuntimeError(
                    "Higgsfield browser Clerk signup HTTP 400: "
                    "{'errors':[{'code':'captcha_invalid'}]}"
                )
            return Account(
                platform="higg",
                email="higg-risk@example.com",
                password="",
                user_id="user-higg",
                token="session",
                status=AccountStatus.REGISTERED,
                extra={"cookies": "__client=client; datadome=dd"},
            )

    def fake_build_platform_instance(
        platform_name,
        payload,
        logger,
        resolved_proxy=None,
        shared_mailbox=None,
    ):
        resolved.append(resolved_proxy)
        return FakePlatform(resolved_proxy)

    monkeypatch.setattr(tasks, "_build_platform_instance", fake_build_platform_instance)

    logger = _Logger()
    tasks._execute_register_task(
        {
            "platform": "higg",
            "count": 1,
            "concurrency": 1,
            "executor_type": "protocol",
            "use_proxy_pool": True,
            "proxy_retry_attempts": 2,
            "extra": {
                "identity_provider": "manual_phone",
                "higg_proxy_reuse_cooldown_seconds": 120,
            },
        },
        logger,
    )

    assert logger.finished == tasks.TASK_STATUS_SUCCEEDED
    assert resolved == [
        "socks5://xray:20101",
        "socks5://xray:20102",
    ]
    assert ("fail", "socks5://xray:20101") in events
    assert ("success", "socks5://xray:20102") in events
    assert any("Higgsfield 风控拒绝" in message for message in logger.messages)


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
