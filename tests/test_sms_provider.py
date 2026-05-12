"""SMS provider unit tests."""
from __future__ import annotations

import pytest
from core.base_sms import (
    HeroSmsProvider,
    HaoZhuMaProvider,
    SmsActivation,
    SmsActivateProvider,
    UOMsgProvider,
    create_sms_provider,
    create_phone_callbacks,
    SMS_ACTIVATE_SERVICES,
    SMS_ACTIVATE_COUNTRIES,
)
import core.base_sms as sms_module


class TestSmsActivateServiceMapping:
    def test_cursor_maps_to_ot(self):
        assert SMS_ACTIVATE_SERVICES["cursor"] == "ot"

    def test_chatgpt_maps_to_dr(self):
        assert SMS_ACTIVATE_SERVICES["chatgpt"] == "dr"

    def test_default_exists(self):
        assert "default" in SMS_ACTIVATE_SERVICES


class TestSmsActivateCountryMapping:
    def test_us_maps_to_187(self):
        assert SMS_ACTIVATE_COUNTRIES["us"] == "187"

    def test_ru_maps_to_0(self):
        assert SMS_ACTIVATE_COUNTRIES["ru"] == "0"

    def test_th_maps_to_52(self):
        assert SMS_ACTIVATE_COUNTRIES["th"] == "52"

    def test_default_exists(self):
        assert "default" in SMS_ACTIVATE_COUNTRIES


class TestCreateSmsProvider:
    def test_sms_activate(self):
        provider = create_sms_provider("sms_activate", {"sms_activate_api_key": "test123"})
        assert isinstance(provider, SmsActivateProvider)
        assert provider.api_key == "test123"

    def test_sms_activate_missing_key(self):
        with pytest.raises(RuntimeError, match="未配置"):
            create_sms_provider("sms_activate", {})

    def test_herosms(self):
        provider = create_sms_provider("herosms", {"herosms_api_key": "hero123"})
        assert isinstance(provider, HeroSmsProvider)
        assert provider.api_key == "hero123"
        assert provider.default_service == "dr"
        assert provider.default_country == "187"

    def test_herosms_reuse_flag_parses_string_false(self):
        provider = create_sms_provider(
            "herosms",
            {
                "herosms_api_key": "hero123",
                "register_reuse_phone_to_max": "false",
            },
        )
        assert isinstance(provider, HeroSmsProvider)
        assert provider.reuse_phone_to_max is False

    def test_herosms_missing_key(self):
        with pytest.raises(RuntimeError, match="HeroSMS 未配置"):
            create_sms_provider("herosms", {})

    def test_uomsg(self):
        provider = create_sms_provider("uomsg_api", {"uomsg_token": "tok123", "uomsg_keyword": "腾讯"})
        assert isinstance(provider, UOMsgProvider)
        assert provider.token == "tok123"
        assert provider.default_keyword == "腾讯"

    def test_uomsg_missing_token(self):
        with pytest.raises(RuntimeError, match="UOMsg 未配置"):
            create_sms_provider("uomsg_api", {})

    def test_haozhuma(self):
        provider = create_sms_provider(
            "haozhuma_api",
            {
                "haozhuma_user": "user1",
                "haozhuma_password": "pass1",
                "haozhuma_cached_token": "cached-token",
                "haozhuma_sid": "1000",
            },
        )
        assert isinstance(provider, HaoZhuMaProvider)
        assert provider.user == "user1"
        assert provider.password == "pass1"
        assert provider.token == "cached-token"
        assert provider.sid == "1000"

    def test_haozhuma_missing_auth(self):
        with pytest.raises(RuntimeError, match="HaoZhuMa 未配置"):
            create_sms_provider("haozhuma_api", {"haozhuma_sid": "1000"})

    def test_unknown_provider(self):
        with pytest.raises(RuntimeError, match="未知"):
            create_sms_provider("unknown", {})


class TestUOMsgProvider:
    def test_get_number_get_code_and_release(self, monkeypatch):
        calls = []

        class FakeResponse:
            def __init__(self, text: str):
                self.text = text

            def raise_for_status(self):
                return None

        def fake_get(url, params=None, timeout=20, proxies=None):
            calls.append((url, dict(params or {}), timeout, proxies))
            code = (params or {}).get("code")
            if code == "getPhone":
                return FakeResponse("16512345678")
            if code == "getMsg":
                return FakeResponse("【腾讯科技】验证码123456，用于登录")
            if code == "release":
                return FakeResponse("释放成功")
            raise AssertionError(f"unexpected code: {code}")

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)

        provider = UOMsgProvider("tok", default_keyword="腾讯", province="广东", card_type="实卡")
        activation = provider.get_number(service="qq")
        code = provider.get_code(activation.activation_id, timeout=5)

        assert activation.activation_id == "16512345678"
        assert activation.phone_number == "16512345678"
        assert code == "123456"
        assert provider.cancel(activation.activation_id) is True
        assert calls[0][1] == {
            "code": "getPhone",
            "token": "tok",
            "keyWord": "腾讯",
            "province": "广东",
            "cardType": "实卡",
        }
        assert calls[1][1]["code"] == "getMsg"
        assert calls[1][1]["phone"] == "16512345678"
        assert calls[1][1]["keyWord"] == "腾讯"
        assert calls[2][1] == {"code": "release", "token": "tok", "phone": "16512345678"}

    def test_maps_qq_service_to_tencent_keyword(self, monkeypatch):
        class FakeResponse:
            text = "16512345678"

            def raise_for_status(self):
                return None

        seen = {}

        def fake_get(url, params=None, timeout=20, proxies=None):
            seen.update(params or {})
            return FakeResponse()

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)

        provider = UOMsgProvider("tok")
        activation = provider.get_number(service="qq")
        sms_module._release_sms_number("uomsg", activation.activation_id)

        assert seen["keyWord"] == "腾讯"

    def test_duplicate_active_number_is_retried_once(self, monkeypatch):
        responses = ["16511111111", "16511111111", "16522222222"]
        calls = []

        class FakeResponse:
            def __init__(self, text: str):
                self.text = text

            def raise_for_status(self):
                return None

        def fake_get(url, params=None, timeout=20, proxies=None):
            calls.append(dict(params or {}))
            code = (params or {}).get("code")
            if code == "getPhone":
                return FakeResponse(responses.pop(0))
            if code == "release":
                return FakeResponse("release ok")
            raise AssertionError(f"unexpected code: {code}")

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)

        provider_a = UOMsgProvider("tok", default_keyword="qq")
        provider_b = UOMsgProvider("tok", default_keyword="qq")
        activation_a = provider_a.get_number(service="qq")
        activation_b = provider_b.get_number(service="qq")

        assert activation_a.phone_number == "16511111111"
        assert activation_b.phone_number == "16522222222"
        assert [call["code"] for call in calls[:3]] == ["getPhone", "getPhone", "getPhone"]

        provider_a.cancel(activation_a.activation_id)
        provider_b.cancel(activation_b.activation_id)

    def test_get_code_after_ignores_old_message(self, monkeypatch):
        messages = [
            "【腾讯科技】验证码111111，用于登录",
            "【腾讯科技】验证码111111，用于登录",
            "【腾讯科技】验证码222222，用于登录",
        ]

        class FakeResponse:
            def __init__(self, text: str):
                self.text = text

            def raise_for_status(self):
                return None

        def fake_get(url, params=None, timeout=20, proxies=None):
            assert (params or {}).get("code") == "getMsg"
            return FakeResponse(messages.pop(0))

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)
        monkeypatch.setattr("core.base_sms.time.sleep", lambda seconds: None)

        provider = UOMsgProvider("tok", default_keyword="腾讯")

        assert provider.get_code_after("16512345678", timeout=5, ignore_text="【腾讯科技】验证码111111，用于登录") == "222222"

    def test_get_code_after_continues_after_request_timeout(self, monkeypatch):
        responses = [
            sms_module.requests.exceptions.ReadTimeout("read timeout"),
            "[尚未收到]",
            "【腾讯科技】验证码333333，用于登录",
        ]

        class FakeResponse:
            def __init__(self, text: str):
                self.text = text

            def raise_for_status(self):
                return None

        def fake_get(url, params=None, timeout=20, proxies=None):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return FakeResponse(item)

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)
        monkeypatch.setattr("core.base_sms.time.sleep", lambda seconds: None)

        provider = UOMsgProvider("tok", default_keyword="腾讯")

        assert provider.get_code_after("16512345678", timeout=5) == "333333"

    def test_get_code_after_waits_for_new_message_after_unparsable_sms(self, monkeypatch):
        messages = [
            "【腾讯科技】登录提醒，请勿转发",
            "【腾讯科技】登录提醒，请勿转发",
            "【腾讯科技】验证码444444，用于登录",
        ]

        class FakeResponse:
            def __init__(self, text: str):
                self.text = text

            def raise_for_status(self):
                return None

        def fake_get(url, params=None, timeout=20, proxies=None):
            return FakeResponse(messages.pop(0))

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)
        monkeypatch.setattr("core.base_sms.time.sleep", lambda seconds: None)

        provider = UOMsgProvider("tok", default_keyword="腾讯")

        assert provider.get_code_after("16512345678", timeout=5) == "444444"


class TestHaoZhuMaProvider:
    def test_get_number_get_code_release_and_blacklist(self, monkeypatch):
        calls = []

        class FakeResponse:
            def __init__(self, data):
                self._data = data
                self.text = str(data)

            def raise_for_status(self):
                return None

            def json(self):
                return dict(self._data)

        def fake_get(url, params=None, timeout=20, proxies=None):
            calls.append((url, dict(params or {}), timeout, proxies))
            api = (params or {}).get("api")
            if api == "login":
                return FakeResponse({"code": "0", "msg": "success", "token": "tok123"})
            if api == "getPhone":
                return FakeResponse({"code": "0", "sid": "1000", "phone": "16512345678", "country_code": "cn"})
            if api == "getMessage":
                return FakeResponse({"code": "0", "sms": "【腾讯】验证码为：654321", "yzm": "654321"})
            if api == "cancelRecv":
                return FakeResponse({"code": "0", "msg": "释放成功"})
            if api == "addBlacklist":
                return FakeResponse({"code": "0", "msg": "success"})
            raise AssertionError(f"unexpected api: {api}")

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)

        stored_tokens = []
        provider = HaoZhuMaProvider(
            user="user1",
            password="pass1",
            sid="1000",
            province="44",
            poll_interval=1,
            token_store=stored_tokens.append,
        )
        activation = provider.get_number(service="qq")
        assert activation.activation_id == "16512345678"
        assert activation.metadata["sid"] == "1000"
        assert provider.get_code(activation.activation_id, timeout=5) == "654321"
        assert provider.report_success(activation.activation_id) is True
        assert calls[0][1] == {"api": "login", "user": "user1", "pass": "pass1"}
        assert calls[1][1] == {"api": "getPhone", "token": "tok123", "sid": "1000", "Province": "44"}
        assert calls[2][1] == {"api": "getMessage", "token": "tok123", "sid": "1000", "phone": "16512345678"}
        assert calls[3][1] == {"api": "cancelRecv", "token": "tok123", "sid": "1000", "phone": "16512345678"}
        assert calls[4][1] == {"api": "addBlacklist", "token": "tok123", "sid": "1000", "phone": "16512345678"}
        assert len([call for call in calls if call[1]["api"] == "login"]) == 1
        assert stored_tokens == ["tok123"]
        provider.cancel(activation.activation_id)
        assert len([call for call in calls if call[1]["api"] == "cancelRecv"]) == 1

    def test_get_number_uses_batch_param_and_skips_active_candidate(self, monkeypatch):
        calls = []

        class FakeResponse:
            def __init__(self, data):
                self._data = data
                self.text = str(data)

            def raise_for_status(self):
                return None

            def json(self):
                return dict(self._data)

        def fake_get(url, params=None, timeout=20, proxies=None):
            calls.append(dict(params or {}))
            api = (params or {}).get("api")
            if api == "getPhone" and len([call for call in calls if call["api"] == "getPhone"]) == 1:
                return FakeResponse({"code": "0", "sid": "1000", "phone": "16511111111"})
            if api == "getPhone":
                return FakeResponse({"code": "0", "sid": "1000", "phone": ["16511111111", "16522222222"]})
            if api in {"cancelRecv", "addBlacklist"}:
                return FakeResponse({"code": "0", "msg": "ok"})
            raise AssertionError(f"unexpected api: {api}")

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)

        provider_a = HaoZhuMaProvider(token="tok123", sid="1000", batch_size=5)
        provider_b = HaoZhuMaProvider(token="tok123", sid="1000", batch_size=5)
        activation_a = provider_a.get_number(service="qq")
        activation_b = provider_b.get_number(service="qq")

        assert activation_a.phone_number == "16511111111"
        assert activation_b.phone_number == "16522222222"
        get_phone_calls = [call for call in calls if call["api"] == "getPhone"]
        assert get_phone_calls[0]["num"] == "5"
        assert get_phone_calls[1]["num"] == "5"

        provider_a.cancel(activation_a.activation_id)
        provider_b.cancel(activation_b.activation_id)

    def test_cached_token_is_refreshed_once_when_rejected(self, monkeypatch):
        calls = []

        class FakeResponse:
            def __init__(self, data):
                self._data = data
                self.text = str(data)

            def raise_for_status(self):
                return None

            def json(self):
                return dict(self._data)

        def fake_get(url, params=None, timeout=20, proxies=None):
            calls.append(dict(params or {}))
            api = (params or {}).get("api")
            if api == "getSummary" and (params or {}).get("token") == "stale-token":
                return FakeResponse({"code": "-1", "msg": "token invalid"})
            if api == "login":
                return FakeResponse({"code": "0", "msg": "success", "token": "fresh-token"})
            if api == "getSummary":
                return FakeResponse({"code": "0", "money": "36.00", "num": 50})
            raise AssertionError(f"unexpected api: {api}")

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)
        stored_tokens = []

        provider = HaoZhuMaProvider(
            user="user1",
            password="pass1",
            token="stale-token",
            sid="1000",
            token_store=stored_tokens.append,
        )

        assert provider.get_balance() == 36.0
        assert [call["api"] for call in calls] == ["getSummary", "login", "getSummary"]
        assert calls[0]["token"] == "stale-token"
        assert calls[2]["token"] == "fresh-token"
        assert stored_tokens == ["fresh-token"]

    def test_factory_persists_login_token_to_provider_setting(self, monkeypatch):
        from infrastructure.provider_settings_repository import ProviderSettingsRepository

        class FakeResponse:
            def __init__(self, data):
                self._data = data
                self.text = str(data)

            def raise_for_status(self):
                return None

            def json(self):
                return dict(self._data)

        def fake_get(url, params=None, timeout=20, proxies=None):
            api = (params or {}).get("api")
            if api == "login":
                return FakeResponse({"code": "0", "msg": "success", "token": "persisted-token"})
            if api == "getSummary":
                return FakeResponse({"code": "0", "money": "36.00", "num": 50})
            raise AssertionError(f"unexpected api: {api}")

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)

        repo = ProviderSettingsRepository()
        repo.save(
            setting_id=None,
            provider_type="sms",
            provider_key="haozhuma_api",
            display_name="HaoZhuMa",
            auth_mode="password",
            enabled=True,
            is_default=True,
            config={"haozhuma_sid": "1000"},
            auth={"haozhuma_user": "user1", "haozhuma_password": "pass1"},
            metadata={},
        )
        settings = repo.resolve_runtime_settings("sms", "haozhuma_api", {})

        provider = create_sms_provider("haozhuma_api", settings)
        assert provider.get_balance() == 36.0

        saved = repo.get_by_key("sms", "haozhuma_api")
        assert saved is not None
        assert saved.get_auth()["haozhuma_cached_token"] == "persisted-token"

    def test_timeout_releases_and_blacklists(self, monkeypatch):
        calls = []

        class FakeResponse:
            def __init__(self, data):
                self._data = data
                self.text = str(data)

            def raise_for_status(self):
                return None

            def json(self):
                return dict(self._data)

        def fake_get(url, params=None, timeout=20, proxies=None):
            calls.append(dict(params or {}))
            api = (params or {}).get("api")
            if api == "getMessage":
                return FakeResponse({"code": "0", "sms": "", "yzm": ""})
            return FakeResponse({"code": "0", "msg": "ok"})

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)
        monkeypatch.setattr("core.base_sms.time.sleep", lambda seconds: None)

        provider = HaoZhuMaProvider(user="user1", password="pass1", token="tok123", sid="1000", poll_interval=1)
        provider._activation_sids["16512345678"] = "1000"

        assert provider.get_code("16512345678", timeout=0) == ""
        assert [call["api"] for call in calls] == ["cancelRecv", "addBlacklist"]


class TestCreatePhoneCallbacks:
    def test_returns_tuple(self):
        # This will fail on actual API call, but we can test the structure
        callback, cleanup = create_phone_callbacks(
            "sms_activate",
            {"sms_activate_api_key": "test"},
            service="cursor",
        )
        assert callable(callback)
        assert callable(cleanup)

    def test_provider_is_created_lazily_and_cleanup_cancels_pending_activation(self, monkeypatch):
        events = []
        logs = []

        class FakeProvider:
            def get_number(self, *, service: str, country: str = ""):
                events.append(("get_number", service, country))
                return SmsActivation(activation_id="act_1", phone_number="+15551234567")

            def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
                events.append(("get_code", activation_id, timeout))
                return ""

            def cancel(self, activation_id: str) -> bool:
                events.append(("cancel", activation_id))
                return True

            def report_success(self, activation_id: str) -> bool:
                events.append(("report_success", activation_id))
                return True

        monkeypatch.setattr("core.base_sms.create_sms_provider", lambda provider_key, config: FakeProvider())

        callback, cleanup = create_phone_callbacks(
            "sms_activate",
            {"sms_activate_api_key": "test"},
            service="chatgpt",
            country="us",
            log_fn=logs.append,
        )

        assert events == []
        assert callback() == "+15551234567"
        cleanup()
        assert ("get_number", "chatgpt", "us") in events
        assert ("cancel", "act_1") in events
        assert any("准备租用手机号" in item for item in logs)
        assert any("已成功租到号码" in item for item in logs)
        assert any("已释放未使用号码" in item for item in logs)

    def test_cleanup_does_not_cancel_after_success(self, monkeypatch):
        events = []
        logs = []

        class FakeProvider:
            def get_number(self, *, service: str, country: str = ""):
                events.append(("get_number", service, country))
                return SmsActivation(activation_id="act_2", phone_number="+15557654321")

            def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
                events.append(("get_code", activation_id, timeout))
                return "123456"

            def cancel(self, activation_id: str) -> bool:
                events.append(("cancel", activation_id))
                return True

            def report_success(self, activation_id: str) -> bool:
                events.append(("report_success", activation_id))
                return True

        monkeypatch.setattr("core.base_sms.create_sms_provider", lambda provider_key, config: FakeProvider())

        callback, cleanup = create_phone_callbacks(
            "sms_activate",
            {"sms_activate_api_key": "test"},
            service="chatgpt",
            log_fn=logs.append,
        )

        assert callback() == "+15557654321"
        assert callback() == "123456"
        cleanup()
        assert ("report_success", "act_2") in events
        assert ("cancel", "act_2") not in events
        assert any("等待短信验证码" in item for item in logs)
        assert any("短信验证成功" in item for item in logs)

    def test_deferred_success_provider_reports_on_cleanup_for_legacy_callers(self, monkeypatch):
        events = []

        class FakeProvider:
            auto_report_success_on_code = False

            def get_number(self, *, service: str, country: str = ""):
                events.append(("get_number", service, country))
                return SmsActivation(activation_id="act_deferred", phone_number="+15550001111")

            def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
                events.append(("get_code", activation_id, timeout))
                return "111222"

            def cancel(self, activation_id: str) -> bool:
                events.append(("cancel", activation_id))
                return True

            def report_success(self, activation_id: str) -> bool:
                events.append(("report_success", activation_id))
                return True

        monkeypatch.setattr("core.base_sms.create_sms_provider", lambda provider_key, config: FakeProvider())

        callback, cleanup = create_phone_callbacks(
            "herosms",
            {"herosms_api_key": "test"},
            service="cursor",
        )

        assert callback() == "+15550001111"
        assert callback() == "111222"
        cleanup()
        assert ("report_success", "act_deferred") in events
        assert ("cancel", "act_deferred") not in events

    def test_first_number_fetch_failure_does_not_poison_future_retries(self, monkeypatch):
        events = []

        class FakeProvider:
            def __init__(self):
                self.calls = 0

            def get_number(self, *, service: str, country: str = ""):
                self.calls += 1
                events.append(("get_number", self.calls, service, country))
                if self.calls == 1:
                    raise RuntimeError("temporary failure")
                return SmsActivation(activation_id="act_retry", phone_number="+66123456789")

            def get_code(self, activation_id: str, *, timeout: int = 120) -> str:
                events.append(("get_code", activation_id, timeout))
                return "654321"

            def cancel(self, activation_id: str) -> bool:
                events.append(("cancel", activation_id))
                return True

            def report_success(self, activation_id: str) -> bool:
                events.append(("report_success", activation_id))
                return True

        provider = FakeProvider()
        monkeypatch.setattr("core.base_sms.create_sms_provider", lambda provider_key, config: provider)

        callback, cleanup = create_phone_callbacks(
            "sms_activate",
            {"sms_activate_api_key": "test"},
            service="chatgpt",
            country="th",
        )

        with pytest.raises(RuntimeError, match="temporary failure"):
            callback()

        assert callback() == "+66123456789"
        assert callback() == "654321"
        cleanup()
        assert ("report_success", "act_retry") in events

    def test_herosms_number_fetch_failure_releases_verify_lock(self, monkeypatch):
        class FakeProvider:
            def get_number(self, *, service: str, country: str = ""):
                raise RuntimeError("temporary failure")

        monkeypatch.setattr("core.base_sms.create_sms_provider", lambda provider_key, config: FakeProvider())

        callback, cleanup = create_phone_callbacks(
            "herosms",
            {"herosms_api_key": "test"},
            service="chatgpt",
        )

        with pytest.raises(RuntimeError, match="temporary failure"):
            callback()

        assert callback._verify_lock_acquired is False
        cleanup()

    def test_mark_send_succeeded_delegates_to_provider(self, monkeypatch):
        events = []

        class FakeProvider:
            def get_number(self, *, service: str, country: str = ""):
                return SmsActivation(activation_id="act_sent", phone_number="+15551234567")

            def mark_send_succeeded(self, activation_id: str) -> None:
                events.append(("mark_send_succeeded", activation_id))

            def cancel(self, activation_id: str) -> bool:
                events.append(("cancel", activation_id))
                return True

        monkeypatch.setattr("core.base_sms.create_sms_provider", lambda provider_key, config: FakeProvider())

        callback, cleanup = create_phone_callbacks(
            "herosms",
            {"herosms_api_key": "test"},
            service="chatgpt",
        )

        assert callback() == "+15551234567"
        callback.mark_send_succeeded()
        cleanup()
        assert ("mark_send_succeeded", "act_sent") in events


class TestSmsActivateProviderCountryResolution:
    def test_get_number_accepts_numeric_country_id(self, monkeypatch):
        captured = {}

        def fake_request(self, action: str, **params):
            captured["action"] = action
            captured["params"] = params
            return "NO_NUMBERS"

        monkeypatch.setattr(SmsActivateProvider, "_request", fake_request)
        provider = SmsActivateProvider("test123", default_country="ru")

        with pytest.raises(RuntimeError, match="NO_NUMBERS|无可用号码"):
            provider.get_number(service="chatgpt", country="52")

        assert captured["action"] == "getNumber"
        assert captured["params"]["country"] == "52"


class TestHeroSmsProvider:
    def test_get_number_uses_v2_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sms_module, "hero_sms_cache_file", lambda: tmp_path / ".herosms_phone_cache.json")
        monkeypatch.setattr(sms_module, "_HERO_SMS_CACHE", None)
        calls = []

        class FakeResp:
            text = '{"activationId":"act_1","phoneNumber":"5551234","countryPhoneCode":"1","activationCost":"0.6"}'

            def raise_for_status(self):
                return None

            def json(self):
                return {"activationId": "act_1", "phoneNumber": "5551234", "countryPhoneCode": "1", "activationCost": "0.6"}

        def fake_get(url, params, timeout=30, proxies=None):
            calls.append(params)
            return FakeResp()

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)
        provider = HeroSmsProvider("hero123")
        activation = provider.get_number(service="chatgpt", country="187")

        assert activation.activation_id == "act_1"
        assert activation.phone_number == "+15551234"
        assert calls[0]["action"] == "getNumberV2"

    def test_get_number_falls_back_to_v1_text(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sms_module, "hero_sms_cache_file", lambda: tmp_path / ".herosms_phone_cache.json")
        monkeypatch.setattr(sms_module, "_HERO_SMS_CACHE", None)
        calls = []

        class FakeResp:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("not json")

        def fake_get(url, params, timeout=30, proxies=None):
            calls.append(params["action"])
            if params["action"] == "getNumberV2":
                return FakeResp("BAD")
            return FakeResp("ACCESS_NUMBER:act_2:15557654321")

        monkeypatch.setattr("core.base_sms.requests.get", fake_get)
        provider = HeroSmsProvider("hero123")
        activation = provider.get_number(service="chatgpt", country="187")

        assert activation.activation_id == "act_2"
        assert activation.phone_number == "+15557654321"
        assert calls == ["getNumberV2", "getNumber"]

    def test_get_code_skips_attempted_sms_event(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sms_module, "hero_sms_cache_file", lambda: tmp_path / ".herosms_phone_cache.json")
        monkeypatch.setattr(sms_module, "_HERO_SMS_CACHE", {
            "api_key_hash": sms_module._hash_secret("hero123"),
            "service": "dr",
            "country": "187",
            "activation_id": "act_3",
            "phone_number": "+15550000000",
            "acquired_at": sms_module.time.time(),
            "use_count": 0,
            "used_codes": set(),
            "attempted_sms_keys": set(),
            "reuse_stopped": False,
        })
        provider = HeroSmsProvider("hero123")
        first = {"status": "ok", "code": "111111", "sms_key": "sms_1", "allow_same_code": True}
        second = {"status": "ok", "code": "222222", "sms_key": "sms_2", "allow_same_code": True}
        results = [first, second]

        monkeypatch.setattr(provider, "get_status_v2", lambda activation_id: results.pop(0))
        monkeypatch.setattr(provider, "get_status", lambda activation_id: {"status": "wait_code"})
        monkeypatch.setattr(provider, "get_active_activations", lambda: [])
        monkeypatch.setattr(provider, "request_resend_sms", lambda activation_id: True)

        assert provider.get_code("act_3", timeout=1) == "111111"
        provider.mark_code_failed("act_3", "invalid otp")
        assert provider.get_code("act_3", timeout=1) == "222222"

    def test_mark_send_succeeded_sets_sms_sent_status(self, monkeypatch):
        calls = []
        provider = HeroSmsProvider("hero123")
        monkeypatch.setattr(provider, "set_status", lambda activation_id, status: calls.append((activation_id, status)) or "ACCESS_READY")

        provider.mark_send_succeeded("act_4")

        assert calls == [("act_4", 1)]

    def test_mark_code_failed_triggers_openai_and_herosms_resend(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sms_module, "hero_sms_cache_file", lambda: tmp_path / ".herosms_phone_cache.json")
        monkeypatch.setattr(sms_module, "_HERO_SMS_CACHE", {
            "api_key_hash": sms_module._hash_secret("hero123"),
            "service": "dr",
            "country": "187",
            "activation_id": "act_5",
            "phone_number": "+15550000000",
            "acquired_at": sms_module.time.time(),
            "use_count": 0,
            "used_codes": set(),
            "attempted_sms_keys": set(),
            "reuse_stopped": False,
        })
        events = []
        provider = HeroSmsProvider("hero123")
        provider.last_code_result = {"code": "333333", "sms_key": "sms_3"}
        provider.set_resend_callback(lambda: events.append(("openai_resend",)))
        monkeypatch.setattr(provider, "request_resend_sms", lambda activation_id: events.append(("hero_resend", activation_id)) or True)

        provider.mark_code_failed("act_5", "invalid otp")

        assert ("openai_resend",) in events
        assert ("hero_resend", "act_5") in events
        assert "333333" in sms_module._HERO_SMS_CACHE["used_codes"]
        assert "sms_3" in sms_module._HERO_SMS_CACHE["attempted_sms_keys"]

    def test_report_success_finishes_activation_when_reuse_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sms_module, "hero_sms_cache_file", lambda: tmp_path / ".herosms_phone_cache.json")
        monkeypatch.setattr(sms_module, "_HERO_SMS_CACHE", {
            "api_key_hash": sms_module._hash_secret("hero123"),
            "service": "dr",
            "country": "187",
            "activation_id": "act_6",
            "phone_number": "+15550000000",
            "acquired_at": sms_module.time.time(),
            "use_count": 0,
            "used_codes": set(),
            "attempted_sms_keys": set(),
            "reuse_stopped": False,
        })
        events = []
        provider = HeroSmsProvider("hero123", reuse_phone_to_max=False)
        provider.last_code_result = {"code": "444444", "sms_key": "sms_4"}
        monkeypatch.setattr(provider, "finish_activation", lambda activation_id: events.append(("finish", activation_id)) or True)

        assert provider.report_success("act_6") is True

        assert events == [("finish", "act_6")]
        assert sms_module._HERO_SMS_CACHE is None


class TestSmsActivation:
    def test_dataclass(self):
        a = SmsActivation(activation_id="123", phone_number="+79001234567")
        assert a.activation_id == "123"
        assert a.phone_number == "+79001234567"
        assert a.country == ""

    def test_with_country(self):
        a = SmsActivation(activation_id="1", phone_number="+1555", country="us")
        assert a.country == "us"
