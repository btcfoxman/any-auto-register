from __future__ import annotations

from core.base_platform import Account, RegisterConfig
from core.base_sms import SmsActivation
from core.registry import get, load_all
from infrastructure.platform_runtime import PERSISTED_ACTION_DATA_KEYS, STATEFUL_ACTION_IDS
from platforms.lingya_qq.cookies import LINGYA_QQ_COOKIE_NAMES, build_lingya_qq_account_fields
from platforms.lingya_qq.core import LingYaQQClient
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


def test_lingya_qq_publish_work_flow(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, *, proxy=None, vdevice_guid=None, cookies=None, timeout=20, user_agent=None):
            self.vdevice_guid = vdevice_guid

        def upload_image_bytes(self, image_bytes, *, filename="cover.jpg", content_type=None):
            events.append(("cover", filename, content_type, image_bytes))
            return "https://filecdn.lumio.qq.com/image/cover.jpg"

        def upload_video_bytes(self, video_bytes, *, filename="video.mp4", vuid, seq=None, chunk_size=1024 * 1024):
            events.append(("video", filename, vuid, video_bytes))
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
    monkeypatch.setattr(
        "platforms.lingya_qq.plugin.fetch_lingya_qq_publish_asset",
        lambda *args, **kwargs: LingYaQQPublishAsset(
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
        ),
    )

    platform = LingYaQQPlatform(
        config=RegisterConfig(
            executor_type="manual_assisted",
            extra={"lingya_qq_publish_source_url": "https://example.com/work"},
        )
    )
    account = Account(
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        user_id="vuid",
        extra={"cookies": "v_vusession=session; v_vuserid=vuid; vdevice_guid=device"},
    )

    result = platform.execute_action(
        "publish_work",
        account,
        {
            "initial_delay": "0",
            "poll_interval": "0",
            "timeout": "1",
            "generation_timeout": "1",
            "generation_poll_interval": "0",
        },
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["last_publish_vid"] == "vid123"
    assert data["last_publish_status"] == "released"
    assert data["quota_balance"] == "300"
    assert ("upload_work", 2, "vid123", "publish title") in events
    assert ("upload_work", 1, "vid123", "publish title") in events
