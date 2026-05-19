from __future__ import annotations

from core.base_platform import Account, RegisterConfig
from core.base_sms import SmsActivation
from core.registry import get, load_all
from infrastructure.platform_runtime import PERSISTED_ACTION_DATA_KEYS, STATEFUL_ACTION_IDS, _build_account_overview
from platforms.lingya_qq.cookies import LINGYA_QQ_COOKIE_NAMES, build_lingya_qq_account_fields
from platforms.lingya_qq.core import DEFAULT_VIDEO_UPLOAD_SERVICE_ID, DIRECT_UPLOAD_PROXIES, LingYaQQClient
from platforms.lingya_qq.plugin import (
    LingYaQQPlatform,
    _generate_lingya_profile_nickname,
    _is_lingya_sms_code,
    _resolve_sms_runtime,
    _sms_timeout,
)
from platforms.lingya_qq.publish import LingYaQQPublishAsset


def test_lingya_qq_is_registered():
    load_all()
    assert get("lingya_qq") is LingYaQQPlatform


def test_lingya_qq_resolves_uomsg_inline_token():
    provider_key, settings = _resolve_sms_runtime({"uomsg_token": "tok123"})

    assert provider_key == "uomsg_api"
    assert settings["uomsg_token"] == "tok123"


def test_lingya_qq_resolves_eomsg_inline_token():
    provider_key, settings = _resolve_sms_runtime({"eomsg_token": "tok123"})

    assert provider_key == "eomsg_api"
    assert settings["eomsg_token"] == "tok123"


def test_lingya_qq_resolves_feihumsg_inline_auth():
    provider_key, settings = _resolve_sms_runtime({
        "feihumsg_user": "user1",
        "feihumsg_password": "pass1",
        "feihumsg_pid": "1001",
    })

    assert provider_key == "feihumsg_api"
    assert settings["feihumsg_user"] == "user1"
    assert settings["feihumsg_pid"] == "1001"


def test_lingya_qq_normalizes_haozhuma_provider_alias():
    provider_key, settings = _resolve_sms_runtime({"sms_provider": "HaoZhuMa", "haozhuma_sid": "1000"})

    assert provider_key == "haozhuma_api"
    assert settings["haozhuma_sid"] == "1000"


def test_lingya_qq_exposes_relogin_sms_action():
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    actions = platform.get_platform_actions()

    assert any(item["id"] == "relogin_sms" for item in actions)
    assert any(item["id"] == "keepalive_sync" for item in actions)
    assert any(item["id"] == "stop_keepalive" for item in actions)
    assert any(item["id"] == "resume_keepalive" for item in actions)
    assert any(item["id"] == "sync_lingya2api" for item in actions)
    assert any(item["id"] == "daily_sign_in" for item in actions)
    assert any(item["id"] == "publish_work" for item in actions)
    publish_action = next(item for item in actions if item["id"] == "publish_work")
    publish_param_keys = {item["key"] for item in publish_action["params"]}
    assert {
        "source_url",
        "source_timeout",
        "source_retries",
        "upload_service_id",
        "creation_process_text",
        "credit_timeout",
        "credit_poll_interval",
        "initial_delay",
        "poll_interval",
        "timeout",
    } <= publish_param_keys
    assert "relogin_sms" in STATEFUL_ACTION_IDS
    assert "keepalive_sync" in STATEFUL_ACTION_IDS
    assert "stop_keepalive" in STATEFUL_ACTION_IDS
    assert "resume_keepalive" in STATEFUL_ACTION_IDS
    assert "sync_lingya2api" in STATEFUL_ACTION_IDS
    assert "daily_sign_in" in STATEFUL_ACTION_IDS
    assert "publish_work" in STATEFUL_ACTION_IDS
    assert {"vusession", "vurefresh", "vuid", "vdevice_guid"} <= PERSISTED_ACTION_DATA_KEYS
    assert {"v_vusession", "v_vurefresh", "v_vuserid", "vqq_vusession", "vdevice_guid"} <= PERSISTED_ACTION_DATA_KEYS


def test_lingya_qq_keepalive_preference_actions_update_overview():
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(platform="lingya_qq", email="phone", password="", extra={})

    stop_result = platform.execute_action("stop_keepalive", account, {"reason": "manual review"})
    assert stop_result["ok"] is True
    stop_overview = _build_account_overview("lingya_qq", stop_result["data"])
    assert stop_overview["lingya_keepalive_disabled"] is True
    assert stop_overview["lingya_keepalive_state"] == "disabled"
    assert stop_overview["lingya_keepalive_disabled_reason"] == "manual review"

    resume_result = platform.execute_action("resume_keepalive", account, {})
    assert resume_result["ok"] is True
    resume_overview = _build_account_overview("lingya_qq", resume_result["data"])
    assert resume_overview["lingya_keepalive_disabled"] is False
    assert resume_overview["lingya_keepalive_state"] == "enabled"
    assert resume_overview["lingya_keepalive_disabled_reason"] == ""


def test_lingya_qq_sms_timeout_is_capped_at_300_seconds():
    assert _sms_timeout("600") == 300
    assert _sms_timeout("12") == 12


def test_lingya_qq_sms_code_validation_rejects_placeholders():
    assert _is_lingya_sms_code("123456") is True
    assert _is_lingya_sms_code("0") is False
    assert _is_lingya_sms_code("abc123") is False
    assert _sms_timeout("") == 300


def test_lingya_qq_profile_nickname_uses_baijiaxing_and_content_char(monkeypatch):
    choices = iter(["赵", "二", "果"])
    monkeypatch.setattr("platforms.lingya_qq.plugin.random.choice", lambda values: next(choices))

    assert _generate_lingya_profile_nickname("清透苹果眼镜", "ignored", "ignored") == "赵二果"


def test_lingya_qq_profile_nickname_falls_back_to_intro_and_prompt(monkeypatch):
    choices = iter(["李", "", "月"])
    monkeypatch.setattr("platforms.lingya_qq.plugin.random.choice", lambda values: next(choices))

    assert _generate_lingya_profile_nickname("Elegant Glasses", "warm island 月光", "ignored") == "李月"
    assert _generate_lingya_profile_nickname("Elegant Glasses", "plain intro", "plain prompt") == ""


def test_lingya_qq_profile_update_failure_is_non_blocking():
    messages = []
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    platform.set_logger(lambda message: messages.append(message))

    class FakeClient:
        def edit_user_profile(self, *, avatar: str, nickname: str = ""):
            raise RuntimeError("profile service unavailable")

    result = platform._update_profile_for_publish(
        FakeClient(),
        avatar_url="https://filecdn.lumio.qq.com/image/cover.jpg",
        title="清透果",
        description="",
        prompt="",
    )

    assert result["profile_updated"] is False
    assert result["profile_update_error"] == "profile service unavailable"
    assert any("profile update skipped" in message for message in messages)


def test_lingya_qq_cookie_header_expands_to_account_fields():
    fields = build_lingya_qq_account_fields(
        {
            "cookies": (
                "v_vusession=session-cookie; v_vurefresh=refresh-cookie; "
                "v_vuserid=vuid-cookie; vdevice_guid=device-cookie; "
                "_qimei_uuid42=qimei-cookie; nick=tester"
            )
        }
    )

    assert len(LINGYA_QQ_COOKIE_NAMES) == 29
    assert fields["vusession"] == "session-cookie"
    assert fields["vurefresh"] == "refresh-cookie"
    assert fields["vuid"] == "vuid-cookie"
    assert fields["v_vusession"] == "session-cookie"
    assert fields["vqq_vusession"] == "session-cookie"
    assert fields["v_vuserid"] == "vuid-cookie"
    assert fields["vdevice_guid"] == "device-cookie"
    assert fields["_qimei_uuid42"] == "qimei-cookie"
    assert "v_vusession=session-cookie" in fields["cookies"]


def test_lingya_qq_cookie_header_percent_encodes_unicode_values():
    fields = build_lingya_qq_account_fields(
        {
            "cookies": "v_vusession=session-cookie; v_vuserid=vuid-cookie; vdevice_guid=device-cookie",
        },
        login_response={
            "vuid": "vuid-cookie",
            "vusession": "session-cookie",
            "user_info": {"user_nick": "腾讯网友"},
        },
    )

    assert fields["nick"] == "腾讯网友"
    assert "nick=腾讯网友" not in fields["cookies"]
    assert "nick=%E8%85%BE%E8%AE%AF%E7%BD%91%E5%8F%8B" in fields["cookies"]

    client = LingYaQQClient(cookies={"vdevice_guid": "device-cookie", "nick": "腾讯网友"})

    assert client.cookie_dict()["nick"] == "%E8%85%BE%E8%AE%AF%E7%BD%91%E5%8F%8B"


def test_lingya_qq_client_accepts_zero_ret_login_response(monkeypatch):
    client = LingYaQQClient(vdevice_guid="device1234567890")
    payload = {
        "ret": 0,
        "msg": "",
        "data": {
            "error_code": 0,
            "error_msg": "",
            "rsp": {"login_response": {"vuid": "vuid-ok", "vusession": "session-ok"}},
        },
    }
    monkeypatch.setattr(client, "_post_pbaccess", lambda path, body: payload)

    result = client.login_with_phone_code(phone="13800138000", code="123456")

    assert result is payload


def test_lingya_qq_refresh_session_writes_browser_like_schedule(monkeypatch):
    client = LingYaQQClient(vdevice_guid="device1234567890")
    monkeypatch.setattr("platforms.lingya_qq.core.time.time", lambda: 1778663095.792)
    monkeypatch.setattr(
        client,
        "_post_pbaccess",
        lambda path, body: {
            "ret": 0,
            "msg": "",
            "data": {
                "error_code": 0,
                "rsp": {
                    "refresh_response": {
                        "vuid": "vuid-ok",
                        "vusession": "session-ok",
                        "vurefresh": "refresh-ok",
                        "vusession_expire_timestamp": "1778670293",
                        "vusession_expire_in": 7200,
                    }
                },
            },
        },
    )

    client.refresh_session(main_login="phone")

    cookies = client.cookie_dict()
    assert cookies["v_main_login"] == "phone"
    assert cookies["last_refresh_second"] == "1778663095"
    assert cookies["_new_next_refresh_time"] == "1778669395792"
    assert cookies["last_refresh_vuserid"] == "vuid-ok"
    assert cookies["video_appid"] == "3000116"
    assert cookies["video_platform"] == "2"
    assert "-" in cookies["last_refresh_time"]


def test_lingya_qq_manual_phone_register(monkeypatch):
    events = []

    class FakeSmsProvider:
        def get_number(self, *, service: str, country: str = ""):
            events.append(("get_number", service, country))
            return SmsActivation(activation_id="act_1", phone_number="+8613800138000")

        def get_code(self, activation_id: str, *, timeout: int = 120):
            events.append(("get_code", activation_id, timeout))
            return "123456"

        def report_success(self, activation_id: str):
            events.append(("report_success", activation_id))
            return True

        def cancel(self, activation_id: str):
            events.append(("cancel", activation_id))
            return True

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid or "device1234567890"

        def login_with_phone_code(self, *, phone: str, code: str, area_code: str = "+86"):
            events.append(("login", phone, code, area_code))
            return {
                "ret": 0,
                "msg": "",
                "data": {
                    "error_code": 0,
                    "error_msg": "",
                    "rsp": {
                        "login_response": {
                            "vuid": "1234567890",
                            "vusession": "session-token",
                            "vurefresh": "refresh-token",
                            "vusession_expire_timestamp": "1778223271",
                            "vusession_expire_in": 7200,
                            "user_info": {"user_nick": "tester", "user_head": ""},
                        }
                    },
                },
            }

        def get_user_profile(self, vuid: str):
            return {
                "ret": 0,
                "data": {
                    "user_item": {
                        "profile_info": {
                            "user_info": {"vuid": vuid, "nickname": "tester", "avatar": ""}
                        }
                    }
                },
            }

        def get_user_quota(self):
            return {"quota_balance": "0", "quota_sum": "0"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.create_sms_provider", lambda key, cfg: FakeSmsProvider())
    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr(
        "platforms.lingya_qq.plugin._resolve_sms_runtime",
        lambda extra: ("sms_activate_api", {"sms_activate_api_key": "key"}),
    )
    monkeypatch.setattr(
        "platforms.lingya_qq.plugin._publish_lingya_phone_login_assist",
        lambda **kwargs: events.append(("assist", kwargs)) or {"assist_id": "assist_1"},
    )

    platform = LingYaQQPlatform(
        config=RegisterConfig(
            executor_type="manual_assisted",
            proxy="http://127.0.0.1:10809",
            extra={"lingya_qq_sms_timeout": "12", "_task_id": "task_1"},
        )
    )
    logs = []
    platform.set_logger(logs.append)
    account = platform.register()

    assert account.platform == "lingya_qq"
    assert account.email == "+8613800138000"
    assert account.user_id == "1234567890"
    assert account.token == "session-token"
    assert account.extra["vdevice_guid"] == "device1234567890"
    assert account.extra["v_vusession"] == "session-token"
    assert account.extra["v_vurefresh"] == "refresh-token"
    assert account.extra["v_vuserid"] == "1234567890"
    assert account.extra["vqq_vusession"] == "session-token"
    assert account.extra["sms_provider"] == "sms_activate_api"
    assert account.extra["phone_provider"] == "sms_activate_api"
    assert account.extra["sms_activation_id"] == "act_1"
    assert account.extra["account_overview"]["sms_provider"] == "sms_activate_api"
    sms_resource = account.extra["provider_resources"][0]
    assert sms_resource["provider_type"] == "sms"
    assert sms_resource["provider_name"] == "sms_activate_api"
    assert sms_resource["resource_identifier"] == "act_1"
    assert "v_vusession=session-token" in account.extra["cookies"]
    assert ("get_number", "qq", "") in events
    assert ("get_code", "act_1", 12) in events
    assert any("register SMS code received: 123456 (len=6)" in message for message in logs)
    assert ("login", "13800138000", "123456", "+86") in events
    assert ("report_success", "act_1") in events
    assist_event = next(item for item in events if item[0] == "assist")
    assert assist_event[1]["phone"] == "+8613800138000"
    assert assist_event[1]["local_phone"] == "13800138000"
    assert assist_event[1]["proxy_url"] == "http://127.0.0.1:10809"
    assert assist_event[1]["timeout_seconds"] == 12
    assert not any(event[0] == "cancel" for event in events)


def test_lingya_qq_check_valid(monkeypatch):
    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            assert vdevice_guid == "device1234567890"
            assert cookies["v_vusession"] == "session-cookie"
            self.vdevice_guid = vdevice_guid
            self._cookies = dict(cookies or {})

        def refresh_session(self, *, main_login: str = "wx"):
            self._cookies.update(
                {
                    "v_vusession": "session-refreshed",
                    "vusession": "session-refreshed",
                    "vqq_vusession": "session-refreshed",
                    "v_vuserid": "1234567890",
                }
            )
            return {
                "ret": 0,
                "data": {
                    "rsp": {
                        "refresh_response": {
                            "vuid": "1234567890",
                            "vusession": "session-refreshed",
                            "vusession_expire_timestamp": "1778227200",
                            "vusession_expire_in": 7200,
                        }
                    }
                },
            }

        def cookie_dict(self):
            return dict(self._cookies)

        def get_user_quota(self):
            return {"quota_balance": "1", "quota_sum": "2"}

        def hello(self):
            return {"timestamp": "1778223271", "token": "hello"}

        def get_user_profile(self, vuid: str):
            return {
                "ret": 0,
                "data": {
                    "user_item": {
                        "profile_info": {
                            "user_info": {"vuid": vuid, "nickname": "tester"}
                        }
                    }
                },
            }

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="1234567890",
        extra={
            "cookies": "v_vusession=session-cookie; v_vuserid=1234567890; vdevice_guid=device1234567890",
        },
    )

    assert platform.check_valid(account) is True
    assert platform.get_last_check_overview()["quota_sum"] == "2"


def test_lingya_qq_check_valid_recovers_from_lingya2api_active_snapshot(monkeypatch):
    class FailingPlatform(LingYaQQPlatform):
        def _load_state(self, account):
            raise RuntimeError("local cookie expired")

    monkeypatch.setattr(
        "platforms.lingya_qq.plugin.get_lingya2api_account_snapshot",
        lambda account, log_fn=None: {
            "status": "active",
            "last_balance": "7",
            "last_quota_sum": "10",
            "last_heartbeat_at": "2026-05-13T00:00:00Z",
        },
    )
    platform = FailingPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={"cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device"},
    )

    assert platform.check_valid(account) is True
    overview = platform.get_last_check_overview()
    assert overview["valid"] is True
    assert overview["lingya2api_status"] == "active"
    assert overview["quota_balance"] == "7"


def test_lingya_qq_relogin_sms_action(monkeypatch):
    events = []

    class FakeSmsProvider:
        def get_number(self, *, service: str, country: str = ""):
            events.append(("get_number", service, country))
            return SmsActivation(
                activation_id="13800138000",
                phone_number="13800138000",
                metadata={"keyword": "è…¾è®¯"},
            )

        def get_message_text(self, activation_id: str):
            events.append(("get_message_text", activation_id))
            return "ã€è…¾è®¯ç§‘æŠ€ã€‘éªŒè¯ç 111111ï¼Œç”¨äºŽç™»å½•"

        def get_code_after(self, activation_id: str, *, timeout: int = 120, ignore_text: str = ""):
            events.append(("get_code_after", activation_id, timeout, ignore_text))
            return "654321"

        def report_success(self, activation_id: str):
            events.append(("report_success", activation_id))
            return True

        def cancel(self, activation_id: str):
            events.append(("cancel", activation_id))
            return True

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            events.append(("client", vdevice_guid))
            self.vdevice_guid = vdevice_guid or "device-new"

        def login_with_phone_code(self, *, phone: str, code: str, area_code: str = "+86"):
            events.append(("login", phone, code, area_code))
            return {
                "ret": 0,
                "data": {
                    "error_code": 0,
                    "rsp": {
                        "login_response": {
                            "vuid": "vuid-new",
                            "vusession": "session-new",
                            "vurefresh": "refresh-new",
                            "vusession_expire_timestamp": "1778223271",
                            "vusession_expire_in": 7200,
                            "user_info": {"user_nick": "tester2", "user_head": ""},
                        }
                    },
                },
            }

        def get_user_profile(self, vuid: str):
            return {
                "ret": 0,
                "data": {
                    "user_item": {
                        "profile_info": {
                            "user_info": {"vuid": vuid, "nickname": "tester2", "avatar": ""}
                        }
                    }
                },
            }

        def get_user_quota(self):
            return {"quota_balance": "3", "quota_sum": "5"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.create_sms_provider", lambda key, cfg: FakeSmsProvider())
    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr("platforms.lingya_qq.plugin.sync_account_to_lingya2api", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "platforms.lingya_qq.plugin._resolve_sms_runtime",
        lambda extra: ("uomsg_api", {"uomsg_token": "tok", "uomsg_keyword": "è…¾è®¯"}),
    )

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    logs = []
    platform.set_logger(logs.append)
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid-old",
        extra={
            "cookies": "v_vusession=session-old; v_vuserid=vuid-old; vdevice_guid=device-old",
            "account_overview": {
                "legacy_extra": {
                    "local_phone": "13800138000",
                    "area_code": "+86",
                    "vdevice_guid": "device-old",
                }
            }
        },
    )

    result = platform.execute_action(
        "relogin_sms",
        account,
        {"sms_timeout": "10", "uomsg_keyword": "è…¾è®¯", "uomsg_province": "å¹¿ä¸œ"},
    )

    assert result["ok"] is True
    assert result["data"]["vusession"] == "session-new"
    assert result["data"]["vurefresh"] == "refresh-new"
    assert result["data"]["vuid"] == "vuid-new"
    assert result["data"]["vdevice_guid"] == "device-old"
    assert result["data"]["v_vusession"] == "session-new"
    assert result["data"]["v_vurefresh"] == "refresh-new"
    assert result["data"]["v_vuserid"] == "vuid-new"
    assert result["data"]["vqq_vusession"] == "session-new"
    assert result["data"]["quota_balance"] == "3"
    assert ("get_number", "qq", "å¹¿ä¸œ") in events
    assert ("get_code_after", "13800138000", 10, "ã€è…¾è®¯ç§‘æŠ€ã€‘éªŒè¯ç 111111ï¼Œç”¨äºŽç™»å½•") in events
    assert any("relogin SMS code received: 654321 (len=6)" in message for message in logs)
    assert ("login", "13800138000", "654321", "+86") in events
    assert ("report_success", "13800138000") in events
    assert not any(event[0] == "cancel" for event in events)
    assert any("old SMS" in item for item in logs)


def test_lingya_qq_relogin_sms_reuses_account_sms_provider(monkeypatch):
    events = []
    resolved_extras = []
    provider_configs = []

    class FakeSmsProvider:
        def get_number(self, *, service: str, country: str = ""):
            events.append(("get_number", service, country))
            return SmsActivation(
                activation_id="13800138000",
                phone_number="13800138000",
                metadata={"sid": "1000", "provider": "haozhuma"},
            )

        def get_code(self, activation_id: str, *, timeout: int = 120):
            events.append(("get_code", activation_id, timeout))
            return "654321"

        def report_success(self, activation_id: str):
            events.append(("report_success", activation_id))
            return True

        def cancel(self, activation_id: str):
            events.append(("cancel", activation_id))
            return True

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid or "device-old"

        def login_with_phone_code(self, *, phone: str, code: str, area_code: str = "+86"):
            events.append(("login", phone, code, area_code))
            return {
                "ret": 0,
                "data": {
                    "error_code": 0,
                    "rsp": {
                        "login_response": {
                            "vuid": "vuid-new",
                            "vusession": "session-new",
                            "vurefresh": "refresh-new",
                        }
                    },
                },
            }

        def get_user_profile(self, vuid: str):
            return {"ret": 0, "data": {"user_item": {"profile_info": {"user_info": {"vuid": vuid}}}}}

        def get_user_quota(self):
            return {"quota_balance": "4", "quota_sum": "6"}

    def fake_resolve_sms_runtime(extra):
        resolved_extras.append(dict(extra))
        return "haozhuma_api", {"haozhuma_sid": extra.get("haozhuma_sid"), "haozhuma_phone": extra.get("haozhuma_phone")}

    def fake_create_sms_provider(key, cfg):
        provider_configs.append((key, dict(cfg)))
        return FakeSmsProvider()

    monkeypatch.setattr("platforms.lingya_qq.plugin.create_sms_provider", fake_create_sms_provider)
    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr("platforms.lingya_qq.plugin._resolve_sms_runtime", fake_resolve_sms_runtime)
    monkeypatch.setattr("platforms.lingya_qq.plugin.sync_account_to_lingya2api", lambda *args, **kwargs: False)

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    logs = []
    platform.set_logger(logs.append)
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid-old",
        extra={
            "cookies": "v_vusession=session-old; v_vuserid=vuid-old; vdevice_guid=device-old",
            "local_phone": "13800138000",
            "area_code": "+86",
            "sms_provider": "haozhuma_api",
            "haozhuma_sid": "1000",
        },
    )

    result = platform.execute_action("relogin_sms", account, {"sms_timeout": "10"})

    assert result["ok"] is True
    assert result["data"]["sms_provider"] == "haozhuma_api"
    assert resolved_extras[-1]["sms_provider"] == "haozhuma_api"
    assert provider_configs[-1][0] == "haozhuma_api"
    assert provider_configs[-1][1]["haozhuma_phone"] == "13800138000"
    assert ("get_number", "1000", "") in events
    assert ("get_code", "13800138000", 10) in events
    assert any("Using HaoZhuMa" in item for item in logs)
    assert not any("Using UOMsg" in item for item in logs)


def test_lingya_qq_relogin_sms_infers_haozhuma_from_account_fields(monkeypatch):
    events = []
    resolved_extras = []

    class FakeSmsProvider:
        def get_number(self, *, service: str, country: str = ""):
            events.append(("get_number", service, country))
            return SmsActivation(activation_id="13800138000", phone_number="13800138000")

        def get_code(self, activation_id: str, *, timeout: int = 120):
            events.append(("get_code", activation_id, timeout))
            return "654321"

        def report_success(self, activation_id: str):
            return True

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid or "device-old"

        def login_with_phone_code(self, *, phone: str, code: str, area_code: str = "+86"):
            return {
                "ret": 0,
                "data": {
                    "error_code": 0,
                    "rsp": {"login_response": {"vuid": "vuid-new", "vusession": "session-new", "vurefresh": "refresh-new"}},
                },
            }

        def get_user_profile(self, vuid: str):
            return {"ret": 0, "data": {"user_item": {"profile_info": {"user_info": {"vuid": vuid}}}}}

        def get_user_quota(self):
            return {"quota_balance": "4", "quota_sum": "6"}

    def fake_resolve_sms_runtime(extra):
        resolved_extras.append(dict(extra))
        return extra.get("sms_provider"), {"haozhuma_sid": extra.get("haozhuma_sid")}

    monkeypatch.setattr("platforms.lingya_qq.plugin.create_sms_provider", lambda key, cfg: FakeSmsProvider())
    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr("platforms.lingya_qq.plugin._resolve_sms_runtime", fake_resolve_sms_runtime)
    monkeypatch.setattr("platforms.lingya_qq.plugin.sync_account_to_lingya2api", lambda *args, **kwargs: False)

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid-old",
        extra={
            "cookies": "v_vusession=session-old; v_vuserid=vuid-old; vdevice_guid=device-old",
            "local_phone": "13800138000",
            "area_code": "+86",
            "haozhuma_sid": "1000",
        },
    )

    result = platform.execute_action("relogin_sms", account, {"sms_timeout": "10"})

    assert result["ok"] is True
    assert resolved_extras[-1]["sms_provider"] == "haozhuma_api"
    assert ("get_number", "1000", "") in events


def test_lingya_qq_relogin_sms_syncs_new_session_to_lingya2api(monkeypatch):
    events = []
    synced = []

    class FakeSmsProvider:
        def get_number(self, *, service: str, country: str = ""):
            return SmsActivation(activation_id="13800138000", phone_number="13800138000")

        def get_code(self, activation_id: str, *, timeout: int = 120):
            return "654321"

        def report_success(self, activation_id: str):
            return True

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid or "device-old"

        def login_with_phone_code(self, *, phone: str, code: str, area_code: str = "+86"):
            return {
                "ret": 0,
                "data": {
                    "error_code": 0,
                    "rsp": {
                        "login_response": {
                            "vuid": "vuid-new",
                            "vusession": "session-new",
                            "vurefresh": "refresh-new",
                        }
                    },
                },
            }

        def get_user_profile(self, vuid: str):
            return {"ret": 0, "data": {"user_item": {"profile_info": {"user_info": {"vuid": vuid}}}}}

        def get_user_quota(self):
            return {"quota_balance": "4", "quota_sum": "6"}

    def fake_sync(account, *, log_fn=None, heartbeat=False, check=False, extra_overrides=None):
        synced.append((account.extra.get("v_vusession"), account.extra.get("v_vurefresh"), heartbeat, check))
        return {"ok": True, "account": {"id": 9}}

    monkeypatch.setattr("platforms.lingya_qq.plugin.create_sms_provider", lambda key, cfg: FakeSmsProvider())
    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr("platforms.lingya_qq.plugin._resolve_sms_runtime", lambda extra: ("uomsg_api", {"uomsg_token": "tok"}))
    monkeypatch.setattr("platforms.lingya_qq.plugin.sync_account_to_lingya2api", fake_sync)

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid-old",
        extra={
            "cookies": "v_vusession=session-old; v_vuserid=vuid-old; vdevice_guid=device-old",
            "local_phone": "13800138000",
            "area_code": "+86",
            "sms_provider": "uomsg_api",
        },
    )

    result = platform.execute_action("relogin_sms", account, {"sms_timeout": "10"})

    assert result["ok"] is True
    assert result["data"]["lingya2api_synced"] is True
    assert synced == [("session-new", "refresh-new", False, False)]


def test_lingya_qq_keepalive_refreshes_and_syncs(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            events.append(("client", vdevice_guid, dict(cookies or {})))
            self.vdevice_guid = vdevice_guid or "device-old"
            self._cookies = dict(cookies or {})

        def hello(self):
            events.append(("hello",))
            return {"timestamp": "1778223000", "token": "hello-token"}

        def refresh_session(self, *, main_login: str = "wx"):
            events.append(("refresh", main_login))
            self._cookies.update(
                {
                    "v_vusession": "session-new",
                    "vusession": "session-new",
                    "vqq_vusession": "session-new",
                    "v_vurefresh": "refresh-new",
                    "v_vuserid": "vuid-new",
                    "vuserid": "vuid-new",
                    "vqq_vuserid": "vuid-new",
                }
            )
            return {
                "ret": 0,
                "data": {
                    "error_code": 0,
                    "rsp": {
                        "refresh_response": {
                            "vuid": "vuid-new",
                            "vusession": "session-new",
                            "vurefresh": "refresh-new",
                            "vusession_expire_timestamp": "1778227200",
                            "vusession_expire_in": 7200,
                        }
                    },
                },
            }

        def cookie_dict(self):
            return dict(self._cookies)

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "8", "quota_sum": "10"}

    def fake_sync(account, *, log_fn=None, heartbeat=False, check=False, extra_overrides=None):
        events.append(("sync", heartbeat, account.extra.get("v_vusession"), account.extra.get("cookies")))
        return {"ok": True, "account": {"id": 7}}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr("platforms.lingya_qq.plugin.sync_account_to_lingya2api", fake_sync)

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid-old",
        extra={
            "cookies": (
                "v_vusession=session-old; v_vurefresh=refresh-old; "
                "v_vuserid=vuid-old; vdevice_guid=device-old; _new_next_refresh_time=1"
            ),
            "v_main_login": "wx",
        },
    )

    result = platform.execute_action("keepalive_sync", account, {})

    assert result["ok"] is True
    data = result["data"]
    assert data["heartbeat_ok"] is True
    assert data["session_refreshed"] is True
    assert data["lingya2api_synced"] is True
    assert data["vusession"] == "session-new"
    assert data["vurefresh"] == "refresh-new"
    assert data["vuid"] == "vuid-new"
    assert data["quota_balance"] == "8"
    assert ("hello",) in events
    assert ("refresh", "wx") in events
    assert events.index(("refresh", "wx")) < events.index(("hello",))
    assert any(event[0] == "sync" and event[1] is False and event[2] == "session-new" for event in events)


def test_lingya_qq_keepalive_quota_ping_can_skip_hello(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid or "device-old"

        def hello(self):
            events.append(("hello",))
            raise AssertionError("quota-only keepalive should not call hello")

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "8", "quota_sum": "10"}

    def fake_sync(account, *, log_fn=None, heartbeat=False, check=False, extra_overrides=None):
        events.append(("sync", heartbeat, account.extra.get("v_vusession")))
        return {"ok": True}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr("platforms.lingya_qq.plugin.sync_account_to_lingya2api", fake_sync)

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid-old",
        extra={
            "cookies": "v_vusession=session-old; v_vurefresh=refresh-old; v_vuserid=vuid-old; vdevice_guid=device-old",
            "v_main_login": "phone",
        },
    )

    result = platform.execute_action("keepalive_sync", account, {"refresh_quota": "true", "run_hello": "false"})

    assert result["ok"] is True
    assert result["data"]["heartbeat_ok"] is True
    assert result["data"]["hello_token_ok"] is None
    assert result["data"]["quota_balance"] == "8"
    assert events == [("quota",), ("sync", False, "session-old")]


def test_lingya_qq_keepalive_refreshes_and_retries_on_hello_session_error(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid or "device-old"
            self._cookies = dict(cookies or {})
            self._hello_calls = 0

        def hello(self):
            self._hello_calls += 1
            events.append(("hello", self._hello_calls))
            if self._hello_calls == 1:
                raise RuntimeError("trpc.workstation.backend.Space/Hello: TRPC error 20447")
            return {"timestamp": "1778223000", "token": "hello-token"}

        def refresh_session(self, *, main_login: str = "wx"):
            events.append(("refresh", main_login))
            self._cookies.update(
                {
                    "v_vusession": "session-new",
                    "vusession": "session-new",
                    "vqq_vusession": "session-new",
                    "v_vurefresh": "refresh-new",
                    "v_vuserid": "vuid-new",
                    "vuserid": "vuid-new",
                    "vqq_vuserid": "vuid-new",
                }
            )
            return {
                "ret": 0,
                "data": {
                    "rsp": {
                        "refresh_response": {
                            "vuid": "vuid-new",
                            "vusession": "session-new",
                            "vurefresh": "refresh-new",
                            "vusession_expire_timestamp": "1778227200",
                            "vusession_expire_in": 7200,
                        }
                    }
                },
            }

        def cookie_dict(self):
            return dict(self._cookies)

        def get_user_quota(self):
            return {"quota_balance": "8", "quota_sum": "10"}

    def fake_sync(account, *, log_fn=None, heartbeat=False, check=False, extra_overrides=None):
        events.append(("sync", heartbeat, account.extra.get("v_vusession")))
        return {"ok": True}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr("platforms.lingya_qq.plugin.sync_account_to_lingya2api", fake_sync)

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid-old",
        extra={
            "cookies": "v_vusession=session-old; v_vurefresh=refresh-old; v_vuserid=vuid-old; vdevice_guid=device-old",
            "v_main_login": "wx",
        },
    )

    result = platform.execute_action("keepalive_sync", account, {"refresh_quota": "false"})

    assert result["ok"] is True
    assert result["data"]["session_refreshed"] is True
    assert result["data"]["hello_token_ok"] is True
    assert result["data"]["v_vusession"] == "session-new"
    assert events[:3] == [("hello", 1), ("refresh", "wx"), ("hello", 2)]
    assert any(event == ("sync", False, "session-new") for event in events)


def test_lingya_qq_keepalive_refreshes_and_retries_on_quota_session_error(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid or "device-old"
            self._cookies = dict(cookies or {})
            self._quota_calls = 0

        def hello(self):
            events.append(("hello",))
            return {"timestamp": "1778223000", "token": "hello-token"}

        def refresh_session(self, *, main_login: str = "wx"):
            events.append(("refresh", main_login))
            self._cookies.update(
                {
                    "v_vusession": "session-new",
                    "vusession": "session-new",
                    "vqq_vusession": "session-new",
                    "v_vurefresh": "refresh-new",
                    "v_vuserid": "vuid-new",
                    "vuserid": "vuid-new",
                    "vqq_vuserid": "vuid-new",
                }
            )
            return {
                "ret": 0,
                "data": {
                    "rsp": {
                        "refresh_response": {
                            "vuid": "vuid-new",
                            "vusession": "session-new",
                            "vurefresh": "refresh-new",
                            "vusession_expire_timestamp": "1778227200",
                            "vusession_expire_in": 7200,
                        }
                    }
                },
            }

        def cookie_dict(self):
            return dict(self._cookies)

        def get_user_quota(self):
            self._quota_calls += 1
            events.append(("quota", self._quota_calls))
            if self._quota_calls == 1:
                raise RuntimeError("trpc.workstation.backend.Space/GetUserQuota: TRPC error 20447")
            return {"quota_balance": "9", "quota_sum": "10"}

    def fake_sync(account, *, log_fn=None, heartbeat=False, check=False, extra_overrides=None):
        events.append(("sync", heartbeat, account.extra.get("v_vusession")))
        return {"ok": True}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr("platforms.lingya_qq.plugin.sync_account_to_lingya2api", fake_sync)

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid-old",
        extra={
            "cookies": "v_vusession=session-old; v_vurefresh=refresh-old; v_vuserid=vuid-old; vdevice_guid=device-old",
            "v_main_login": "wx",
        },
    )

    result = platform.execute_action("keepalive_sync", account, {"refresh_quota": "true"})

    assert result["ok"] is True
    assert result["data"]["session_refreshed"] is True
    assert result["data"]["v_vusession"] == "session-new"
    assert result["data"]["quota_balance"] == "9"
    assert events[:4] == [("hello",), ("quota", 1), ("refresh", "wx"), ("quota", 2)]
    assert any(event == ("sync", False, "session-new") for event in events)


def test_lingya_qq_daily_sign_in_skips_when_already_signed(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid

        def get_credits_panel(self, is_first_register=False):
            events.append(("panel", is_first_register))
            return {"ret": 0, "data": {"items": [{"type": 1, "button_info": {"status": 2, "button_text": "\u5df2\u9886\u53d6"}}]}}

        def credits_panel_sign_in(self):
            events.append(("sign",))
            return {"ret": 0, "data": {"isSignInSuccess": True}}

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "100", "quota_sum": "300"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={"cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device"},
    )

    result = platform.execute_action("daily_sign_in", account, {})

    assert result["ok"] is True
    assert result["data"]["daily_sign_in_status"] == "already_signed"
    assert result["data"]["quota_balance"] == "100"
    assert ("sign",) not in events


def test_lingya_qq_daily_sign_in_disabled_returns_without_client(monkeypatch):
    def fail_client(*args, **kwargs):
        raise AssertionError("client should not be created when sign-in is disabled")

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", fail_client)
    platform = LingYaQQPlatform(
        config=RegisterConfig(
            executor_type="manual_assisted",
            extra={"lingya_qq_daily_sign_in_enabled": "false"},
        )
    )
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={"cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device"},
    )

    result = platform.execute_action("daily_sign_in", account, {})

    assert result["ok"] is True
    assert result["data"]["daily_sign_in_status"] == "disabled"
    assert result["data"]["daily_sign_signed"] is False


def test_lingya_qq_daily_sign_in_claims_when_unused(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid

        def get_credits_panel(self, is_first_register=False):
            events.append(("panel", is_first_register))
            status = 1 if len([event for event in events if event[0] == "panel"]) == 1 else 2
            return {"ret": 0, "data": {"items": [{"type": 1, "button_info": {"status": status, "button_text": "\u9886\u53d6"}}]}}

        def credits_panel_sign_in(self):
            events.append(("sign",))
            return {"ret": 0, "data": {"isSignInSuccess": True}}

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "200", "quota_sum": "300"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={"cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device"},
    )

    result = platform.execute_action("daily_sign_in", account, {})

    assert result["ok"] is True
    assert result["data"]["daily_sign_in_status"] == "signed"
    assert result["data"]["quota_balance"] == "200"
    assert ("sign",) in events


def test_lingya_qq_daily_sign_in_skips_when_panel_unavailable(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid

        def get_credits_panel(self, is_first_register=False):
            events.append(("panel", is_first_register))
            raise RuntimeError("500 Server Error: Internal Server Error")

        def credits_panel_sign_in(self):
            events.append(("sign",))
            return {"ret": 0, "data": {"isSignInSuccess": True}}

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "20", "quota_sum": "300"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={"cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device"},
    )

    result = platform.execute_action("daily_sign_in", account, {})

    assert result["ok"] is True
    assert result["data"]["daily_sign_in_status"] == "panel_unavailable"
    assert result["data"]["daily_sign_error"]
    assert result["data"]["quota_balance"] == "20"
    assert ("sign",) not in events


def test_lingya_qq_sign_in_endpoints_match_observed_flow():
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ret": 0, "data": {}}

    client = LingYaQQClient(vdevice_guid="device")

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    client.session.post = fake_post
    client.get_credits_panel(False)
    client.credits_panel_sign_in()
    client.check_first_post_credit()
    client.check_credits_first_register()
    client.get_user_events()
    client.edit_user_profile(avatar="https://filecdn.lumio.qq.com/image/avatar.png", nickname="赵二果")
    client.edit_user_profile(avatar="https://filecdn.lumio.qq.com/image/avatar-only.png")

    assert calls[0][0].endswith("/trpc.caotai.task_adapter.TaskAdapter/GetCreditsPanel")
    assert calls[0][1]["json"] == {"is_first_register": False}
    assert calls[1][0].endswith("/trpc.caotai.task_adapter.TaskAdapter/CreditsPanelSignIn")
    assert "json" not in calls[1][1]
    assert "Content-Type" not in calls[1][1]["headers"]
    assert calls[2][0].endswith("/trpc.caotai.task_adapter.TaskAdapter/CheckFirstPostCredit")
    assert calls[2][1]["json"] == {}
    assert calls[3][0].endswith("/trpc.caotai.task_adapter.TaskAdapter/CheckCreditsFirstRegister")
    assert "json" not in calls[3][1]
    assert calls[4][0].endswith("/trpc.caotai.account.UserEventService/GetUserEvents")
    assert "json" not in calls[4][1]
    assert calls[5][0].endswith("/trpc.caotai.account.UserProfileService/EditUserProfile")
    assert calls[5][1]["json"]["avatar"] == "https://filecdn.lumio.qq.com/image/avatar.png"
    assert calls[5][1]["json"]["nickname"] == "赵二果"
    assert calls[5][1]["json"]["fields_to_update"] == ["avatar", "nickname"]
    assert calls[6][0].endswith("/trpc.caotai.account.UserProfileService/EditUserProfile")
    assert calls[6][1]["json"]["avatar"] == "https://filecdn.lumio.qq.com/image/avatar-only.png"
    assert "nickname" not in calls[6][1]["json"]
    assert calls[6][1]["json"]["fields_to_update"] == ["avatar"]


def test_lingya_qq_video_upload_uses_observed_service_id_and_sdk_headers(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    client = LingYaQQClient(vdevice_guid="device", proxy="socks5://127.0.0.1:20003")
    monkeypatch.setattr(client, "get_video_upload_params", lambda seq=None: {"seq": "seq-1", "svr_token": "svr-token"})

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/v2/video/prepare"):
            return Response(
                {
                    "code": 0,
                    "fileId": "file.mp4",
                    "ukey": "ukey",
                    "vid": "vid123",
                    "videoId": "video123",
                    "reRoute": {"modid": 65011201, "cmdid": 65536},
                }
            )
        if "/v2/upload/uploadpart" in url:
            return Response({"code": 0, "partSha": "part-sha"})
        if url.endswith("/v2/upload/finishupload"):
            return Response({"code": 0, "msg": "ok"})
        if url.endswith("/v2/video/notifyencode"):
            return Response({"code": 0, "videoId": "video123"})
        raise AssertionError(url)

    client.session.post = fake_post

    result = client.upload_video_bytes(b"video-bytes", filename="video.mp4", vuid="2437301834", chunk_size=1024 * 1024)

    assert result["service_id"] == DEFAULT_VIDEO_UPLOAD_SERVICE_ID
    prepare_url, prepare_kwargs = calls[0]
    assert prepare_url.endswith("/v2/video/prepare")
    assert prepare_kwargs["json"]["serviceId"] == DEFAULT_VIDEO_UPLOAD_SERVICE_ID
    assert prepare_kwargs["headers"]["serviceId"] == DEFAULT_VIDEO_UPLOAD_SERVICE_ID
    assert prepare_kwargs["headers"]["seq"] == "seq-1"
    assert prepare_kwargs["headers"]["upload-uin"] == "2437301834"

    upload_headers = calls[1][1]["headers"]
    assert upload_headers["serviceId"] == DEFAULT_VIDEO_UPLOAD_SERVICE_ID
    assert upload_headers["upload-sid"] == "65011201:65536"
    assert upload_headers["upload-uin"] == "2437301834"
    assert all(item[1].get("proxies") == DIRECT_UPLOAD_PROXIES for item in calls)


def test_lingya_qq_publish_cover_upload_bypasses_account_proxy(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ret": 0, "url": "https://filecdn.lumio.qq.com/image/cover.jpg"}

    client = LingYaQQClient(vdevice_guid="device", proxy="socks5://127.0.0.1:20003")

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    client.session.post = fake_post

    cover_url = client.upload_image_bytes(b"cover", filename="cover.jpg", content_type="image/jpeg")
    data_url_cover = client.upload_image_data_url("data:image/jpeg;base64,Y292ZXI=", filename="highlight.jpg")

    assert cover_url == "https://filecdn.lumio.qq.com/image/cover.jpg"
    assert data_url_cover == "https://filecdn.lumio.qq.com/image/cover.jpg"
    assert all(item[0].startswith("https://fileaccess.lingya.qq.com/upload/image") for item in calls)
    assert all(item[1].get("proxies") == DIRECT_UPLOAD_PROXIES for item in calls)


def test_lingya_qq_publish_work_flow(monkeypatch):
    events = []
    fetch_calls = []
    saved_defaults = []
    upload_payloads = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid
            events.append(("client_proxy", proxy))

        def homepage(self):
            events.append(("homepage",))
            return {"home": {"pid": "project-1"}}

        def refresh_session(self, *, main_login: str = "phone"):
            events.append(("refresh_session", main_login))
            return {"ret": 0, "data": {"rsp": {"refresh_response": {"vusession": "session-refresh"}}}}

        def ack_project(self, project_id: str):
            events.append(("ack", project_id))
            return {}

        def get_user_events(self):
            events.append(("user_events",))
            return {"ret": 0, "data": {"total": 0, "events": []}}

        def check_first_post_credit(self):
            events.append(("first_post_credit",))
            return {"ret": 0, "data": {"is_granted": False, "text": "first post credit"}}

        def upload_image_bytes(self, image_bytes, *, filename="cover.jpg", content_type=None):
            events.append(("cover", filename, content_type, image_bytes))
            return "https://filecdn.lumio.qq.com/image/cover.jpg"

        def edit_user_profile(self, *, avatar: str, nickname: str = ""):
            events.append(("profile", avatar, nickname))
            return {"ret": 0, "data": {}}

        def upload_video_bytes(self, video_bytes, *, filename="video.mp4", vuid, seq=None, service_id=None, chunk_size=1024 * 1024):
            events.append(("video", filename, vuid, video_bytes, service_id))
            return {"vid": "vid123", "file_name": filename}

        def get_work_generation_status(self, vid: str):
            events.append(("generation", vid))
            return {
                "ret": 0,
                "data": {
                    "transcoding_status": 1,
                    "highlight_scene_status": 1,
                    "sequence_frames_status": 1,
                    "highlight_scene_frames_status": 1,
                },
            }

        def get_highlight_scene_list(self, vid: str):
            events.append(("highlight_scene_list", vid))
            return {
                "ret": 0,
                "msg": "",
                "data": {
                    "highlight_segments": [
                        {"start_ms": 0, "end_ms": 1600},
                        {"start_ms": 1600, "end_ms": 16239},
                    ],
                    "highlight_frames_file_data": None,
                },
            }

        def content_security_review(self, text: str):
            events.append(("review", text))
            return {"ret": 0, "data": {"result": 1}}

        def get_cover_color_info(self, *, vid: str, cover_url: str):
            events.append(("color", vid, cover_url))
            return {"ret": 0, "data": {"background_color": "#877f72", "title_color": "#FFFFFF"}}

        def upload_work(self, payload):
            upload_payloads.append(payload)
            events.append(("upload_work", payload["request_type"], payload["vid"], payload["base_info"]["title"]))
            return {"ret": 0, "data": {}}

        def get_my_work_list(self, *, filter_by_status=1, page=1, page_size=15):
            events.append(("work_list", filter_by_status))
            return {"ret": 0, "data": {"work_list": [{"vid": "vid123", "work_status": 1}]}}

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "300", "quota_sum": "300"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    nickname_choices = iter(["赵", "二", "果"])
    monkeypatch.setattr("platforms.lingya_qq.plugin.random.choice", lambda values: next(nickname_choices))

    def fake_fetch_asset(*args, **kwargs):
        fetch_calls.append((args, kwargs))
        return LingYaQQPublishAsset(
            title="publish title",
            description="publish intro 果",
            prompt="first scene prompt",
            video_bytes=b"video-bytes",
            video_filename="video.mp4",
            video_content_type="video/mp4",
            cover_bytes=b"cover-bytes",
            cover_filename="cover.jpg",
            cover_content_type="image/jpeg",
            duration=99,
            cover_ratio=0.75,
            tag_infos=[{"id": "tag_2QCVIf1DjL", "title": "玄幻", "alias": ""}],
            creation_process_text="asset creation process",
        )

    monkeypatch.setattr("platforms.lingya_qq.plugin.fetch_lingya_qq_publish_asset", fake_fetch_asset)
    monkeypatch.setattr("platforms.lingya_qq.plugin._set_global_config_values", lambda values: saved_defaults.append(dict(values)))

    platform = LingYaQQPlatform(
        config=RegisterConfig(
            executor_type="manual_assisted",
            proxy="http://config-proxy.example:8080",
            extra={"lingya_qq_publish_source_url": "https://example.com/work"},
        )
    )
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={
            "cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device",
            "proxy_url": "http://account-proxy.example:8080",
        },
    )

    result = platform.execute_action(
        "publish_work",
        account,
        {
            "initial_delay": "0",
            "source_timeout": "12",
            "source_retries": "4",
            "poll_interval": "0",
            "timeout": "1",
            "generation_timeout": "1",
            "generation_poll_interval": "0",
            "force": "true",
        },
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["last_publish_vid"] == "vid123"
    assert data["last_publish_status"] == "released"
    assert data["profile_updated"] is True
    assert data["avatar"] == "https://filecdn.lumio.qq.com/image/cover.jpg"
    assert data["nick"] == "赵二果"
    assert data["quota_balance"] == "300"
    assert data["lingya_qq_publish_source_url"] == "https://example.com/work"
    assert data["lingya_qq_publish_source_timeout"] == 12
    assert data["lingya_qq_publish_source_retries"] == 4
    assert data["lingya_qq_video_upload_service_id"] == DEFAULT_VIDEO_UPLOAD_SERVICE_ID
    assert saved_defaults[0]["lingya_qq_publish_source_timeout"] == 12
    assert saved_defaults[0]["lingya_qq_video_upload_service_id"] == DEFAULT_VIDEO_UPLOAD_SERVICE_ID
    assert fetch_calls[0][1]["proxy"] is None
    assert fetch_calls[0][1]["timeout"] == 12
    assert fetch_calls[0][1]["retries"] == 4
    assert ("client_proxy", "http://account-proxy.example:8080") in events
    assert events[:5] == [
        ("client_proxy", "http://account-proxy.example:8080"),
        ("homepage",),
        ("refresh_session", "phone"),
        ("ack", "project-1"),
        ("user_events",),
    ]
    assert ("first_post_credit",) in events
    assert events.count(("first_post_credit",)) == 1
    assert ("profile", "https://filecdn.lumio.qq.com/image/cover.jpg", "赵二果") in events
    assert events.index(("profile", "https://filecdn.lumio.qq.com/image/cover.jpg", "赵二果")) < events.index(
        ("video", "video.mp4", "vuid", b"video-bytes", DEFAULT_VIDEO_UPLOAD_SERVICE_ID)
    )
    assert ("video", "video.mp4", "vuid", b"video-bytes", DEFAULT_VIDEO_UPLOAD_SERVICE_ID) in events
    assert ("highlight_scene_list", "vid123") in events
    assert ("upload_work", 2, "vid123", "publish title") in events
    assert ("upload_work", 1, "vid123", "publish title") in events
    final_payload = upload_payloads[-1]
    assert final_payload["base_info"]["description"] == "publish intro 果"
    assert final_payload["base_info"]["duration"] == 16
    assert final_payload["creation_tools"][2]["tools"][0]["title"] == "Seedance 2.0"
    assert final_payload["creation_tools"][2]["tools"][0]["id"] == "tag_8Hy4Gy2MCZ"
    assert final_payload["related_info"]["tag_infos"] == [{"id": "tag_2QCVIf1DjL", "title": "玄幻", "alias": ""}]
    assert final_payload["creation_processes"][0]["extra"] == '{"type":"text","data":{"text":"asset creation process","pureText":true}}'
    assert final_payload["highlight_scenes"][0]["prompt"] == "first scene prompt"
    assert final_payload["highlight_scenes"][0]["server_id"] == ""
    assert final_payload["highlight_scenes"][0]["cover"] == "https://filecdn.lumio.qq.com/image/cover.jpg"
    assert final_payload["highlight_scenes"][0]["video_segments"] == {"start_ms": 0, "end_ms": 1600}
    assert final_payload["highlight_scenes"][0]["key_frame_images"] == []
    assert final_payload["highlight_scenes"][0]["main_tool"][0]["id"] == "tag_8Hy4Gy2MCZ"
    assert final_payload["highlight_scenes"][0]["main_tool"][0]["title"] == "Seedance 2.0"
    assert final_payload["only_self_visible"] is False
    assert data["last_publish_initial_first_post_credit_granted"] is False
    assert data["last_publish_first_post_credit_granted"] is True
    assert data["last_publish_first_post_credit_text"] == "first post credit"


def test_lingya_qq_extracts_first_highlight_segment_from_scene_list():
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))

    segment = platform._first_highlight_segment(
        {
            "ret": 0,
            "data": {
                "highlight_segments": [
                    {"start_ms": 0, "end_ms": 1600},
                    {"start_ms": 1600, "end_ms": 16239},
                ],
                "highlight_frames_file_data": None,
            },
        }
    )

    assert segment == {"start_ms": 0, "end_ms": 1600}
    assert platform._duration_from_highlight_segments(
        {
            "ret": 0,
            "data": {
                "highlight_segments": [
                    {"start_ms": 0, "end_ms": 1600},
                    {"start_ms": 1600, "end_ms": 16239},
                ],
            },
        },
        99,
    ) == 16


def test_lingya_qq_publish_skips_when_local_released(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "99", "quota_sum": "100"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr(
        "platforms.lingya_qq.plugin.fetch_lingya_qq_publish_asset",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch should not run")),
    )

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={
            "cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device",
            "last_publish_status": "released",
        },
    )

    result = platform.execute_action("publish_work", account, {})

    assert result["ok"] is True
    assert result["data"]["publish_skipped"] is True
    assert result["data"]["publish_skip_reason"] == "local_released"
    assert result["data"]["last_publish_status"] == "released"
    assert result["data"]["quota_balance"] == "99"
    assert events == [("quota",)]


def test_lingya_qq_publish_skips_when_remote_released_exists(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid

        def get_my_work_list(self, *, filter_by_status=1, page=1, page_size=15):
            events.append(("work_list", filter_by_status))
            if filter_by_status == 1:
                return {"ret": 0, "data": {"work_list": [{"vid": "old_vid", "work_status": 3, "base_info": {"title": "old title"}}]}}
            return {"ret": 0, "data": {"work_list": []}}

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "88", "quota_sum": "100"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    monkeypatch.setattr(
        "platforms.lingya_qq.plugin.fetch_lingya_qq_publish_asset",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch should not run")),
    )

    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={"cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device"},
    )

    result = platform.execute_action("publish_work", account, {})

    assert result["ok"] is True
    assert result["data"]["publish_skipped"] is True
    assert result["data"]["publish_skip_reason"] == "remote_released"
    assert result["data"]["last_publish_vid"] == "old_vid"
    assert result["data"]["last_publish_title"] == "old title"
    assert result["data"]["quota_balance"] == "88"
    assert events == [("work_list", 1), ("quota",)]


def test_lingya_qq_publish_defaults_are_kept_in_account_overview():
    overview = _build_account_overview(
        "lingya_qq",
        {
            "valid": True,
            "last_publish_status": "released",
            "lingya_qq_publish_source_url": "https://example.com/work",
            "lingya_qq_publish_source_timeout": 12,
            "lingya_qq_publish_source_retries": 4,
            "lingya_qq_publish_initial_delay": 30,
            "lingya_qq_publish_timeout": 7200,
            "lingya_qq_video_upload_service_id": DEFAULT_VIDEO_UPLOAD_SERVICE_ID,
        },
    )

    legacy_extra = overview["legacy_extra"]
    assert legacy_extra["lingya_qq_publish_source_url"] == "https://example.com/work"
    assert legacy_extra["lingya_qq_publish_source_timeout"] == 12
    assert legacy_extra["lingya_qq_publish_source_retries"] == 4
    assert legacy_extra["lingya_qq_video_upload_service_id"] == DEFAULT_VIDEO_UPLOAD_SERVICE_ID
