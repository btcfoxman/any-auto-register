from __future__ import annotations

from core.base_platform import Account, RegisterConfig
from core.base_sms import SmsActivation
from core.registry import get, load_all
from infrastructure.platform_runtime import PERSISTED_ACTION_DATA_KEYS, STATEFUL_ACTION_IDS, _build_account_overview
from platforms.lingya_qq.cookies import LINGYA_QQ_COOKIE_NAMES, build_lingya_qq_account_fields
from platforms.lingya_qq.core import DEFAULT_VIDEO_UPLOAD_SERVICE_ID, LingYaQQClient
from platforms.lingya_qq.plugin import LingYaQQPlatform, _resolve_sms_runtime
from platforms.lingya_qq.publish import LingYaQQPublishAsset


def test_lingya_qq_is_registered():
    load_all()
    assert get("lingya_qq") is LingYaQQPlatform


def test_lingya_qq_resolves_uomsg_inline_token():
    provider_key, settings = _resolve_sms_runtime({"uomsg_token": "tok123"})

    assert provider_key == "uomsg_api"
    assert settings["uomsg_token"] == "tok123"


def test_lingya_qq_exposes_relogin_sms_action():
    platform = LingYaQQPlatform(config=RegisterConfig(executor_type="manual_assisted"))
    actions = platform.get_platform_actions()

    assert any(item["id"] == "relogin_sms" for item in actions)
    assert any(item["id"] == "keepalive_sync" for item in actions)
    assert any(item["id"] == "sync_lingya2api" for item in actions)
    assert any(item["id"] == "daily_sign_in" for item in actions)
    assert any(item["id"] == "publish_work" for item in actions)
    publish_action = next(item for item in actions if item["id"] == "publish_work")
    publish_param_keys = {item["key"] for item in publish_action["params"]}
    assert {"source_url", "source_timeout", "source_retries", "upload_service_id", "initial_delay", "poll_interval", "timeout"} <= publish_param_keys
    assert "relogin_sms" in STATEFUL_ACTION_IDS
    assert "keepalive_sync" in STATEFUL_ACTION_IDS
    assert "sync_lingya2api" in STATEFUL_ACTION_IDS
    assert "daily_sign_in" in STATEFUL_ACTION_IDS
    assert "publish_work" in STATEFUL_ACTION_IDS
    assert {"vusession", "vurefresh", "vuid", "vdevice_guid"} <= PERSISTED_ACTION_DATA_KEYS
    assert {"v_vusession", "v_vurefresh", "v_vuserid", "vqq_vusession", "vdevice_guid"} <= PERSISTED_ACTION_DATA_KEYS


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

    assert len(LINGYA_QQ_COOKIE_NAMES) == 28
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

    platform = LingYaQQPlatform(
        config=RegisterConfig(
            executor_type="manual_assisted",
            extra={"lingya_qq_sms_timeout": "12"},
        )
    )
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
    assert "v_vusession=session-token" in account.extra["cookies"]
    assert ("get_number", "qq", "") in events
    assert ("get_code", "act_1", 12) in events
    assert ("login", "13800138000", "123456", "+86") in events
    assert ("report_success", "act_1") in events
    assert not any(event[0] == "cancel" for event in events)


def test_lingya_qq_check_valid(monkeypatch):
    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            assert vdevice_guid == "device1234567890"
            assert cookies["v_vusession"] == "session-cookie"

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
    assert ("login", "13800138000", "654321", "+86") in events
    assert ("report_success", "13800138000") in events
    assert not any(event[0] == "cancel" for event in events)
    assert any("old SMS" in item for item in logs)


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
    assert any(event[0] == "sync" and event[1] is True and event[2] == "session-new" for event in events)


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

    assert calls[0][0].endswith("/trpc.caotai.task_adapter.TaskAdapter/GetCreditsPanel")
    assert calls[0][1]["json"] == {"is_first_register": False}
    assert calls[1][0].endswith("/trpc.caotai.task_adapter.TaskAdapter/CreditsPanelSignIn")
    assert "json" not in calls[1][1]
    assert "Content-Type" not in calls[1][1]["headers"]


def test_lingya_qq_video_upload_uses_observed_service_id_and_sdk_headers(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    client = LingYaQQClient(vdevice_guid="device")
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


def test_lingya_qq_publish_work_flow(monkeypatch):
    events = []
    fetch_calls = []
    saved_defaults = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid
            events.append(("client_proxy", proxy))

        def upload_image_bytes(self, image_bytes, *, filename="cover.jpg", content_type=None):
            events.append(("cover", filename, content_type, image_bytes))
            return "https://filecdn.lumio.qq.com/image/cover.jpg"

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

        def content_security_review(self, text: str):
            events.append(("review", text))
            return {"ret": 0, "data": {"result": 1}}

        def get_cover_color_info(self, *, vid: str, cover_url: str):
            events.append(("color", vid, cover_url))
            return {"ret": 0, "data": {"background_color": "#877f72", "title_color": "#FFFFFF"}}

        def upload_work(self, payload):
            events.append(("upload_work", payload["request_type"], payload["vid"], payload["base_info"]["title"]))
            return {"ret": 0, "data": {}}

        def get_my_work_list(self, *, filter_by_status=1, page=1, page_size=15):
            events.append(("work_list", filter_by_status))
            return {"ret": 0, "data": {"work_list": [{"vid": "vid123", "work_status": 1}]}}

        def get_user_quota(self):
            events.append(("quota",))
            return {"quota_balance": "300", "quota_sum": "300"}

    monkeypatch.setattr("platforms.lingya_qq.plugin.LingYaQQClient", FakeClient)
    def fake_fetch_asset(*args, **kwargs):
        fetch_calls.append((args, kwargs))
        return LingYaQQPublishAsset(
            title="publish title",
            description="",
            video_bytes=b"video-bytes",
            video_filename="video.mp4",
            video_content_type="video/mp4",
            cover_bytes=b"cover-bytes",
            cover_filename="cover.jpg",
            cover_content_type="image/jpeg",
            duration=16,
            cover_ratio=0.75,
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
    assert ("video", "video.mp4", "vuid", b"video-bytes", DEFAULT_VIDEO_UPLOAD_SERVICE_ID) in events
    assert ("upload_work", 2, "vid123", "publish title") in events
    assert ("upload_work", 1, "vid123", "publish title") in events


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
            if filter_by_status == 3:
                return {"ret": 0, "data": {"work_list": [{"vid": "old_vid", "work_status": 1, "base_info": {"title": "old title"}}]}}
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
    assert events == [("work_list", 3), ("quota",)]


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
