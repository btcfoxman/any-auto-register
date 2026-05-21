from __future__ import annotations

from types import SimpleNamespace

from core.base_platform import RegisterConfig
from core.registration import BrowserRegistrationAdapter, BrowserRegistrationFlow, RegistrationContext, RegistrationResult
import core.registration.helpers as helpers_module
import core.registration.flows as flows_module


def test_browser_flow_wires_phone_callback_and_runs_cleanup(monkeypatch):
    events = []

    def fake_build_phone_callbacks(ctx, *, service=None):
        events.append(("build", service))
        return (lambda: "18885551234", lambda: events.append(("cleanup", service)))

    monkeypatch.setattr(flows_module, "build_phone_callbacks", fake_build_phone_callbacks)

    ctx = RegistrationContext(
        platform_name="chatgpt",
        platform_display_name="ChatGPT",
        platform=SimpleNamespace(mailbox=None),
        identity=SimpleNamespace(
            email="user@example.com",
            has_mailbox=True,
            identity_provider="mailbox",
        ),
        config=RegisterConfig(executor_type="headless", extra={}),
        email="user@example.com",
        password="Secret123!",
        log_fn=lambda message: None,
    )

    def build_worker(ctx, artifacts):
        assert callable(artifacts.phone_callback)
        return SimpleNamespace(phone_callback=artifacts.phone_callback)

    def run_worker(worker, ctx, artifacts):
        events.append(("callback", worker.phone_callback()))
        return {"email": ctx.identity.email, "password": ctx.password}

    adapter = BrowserRegistrationAdapter(
        result_mapper=lambda ctx, raw: RegistrationResult(email=raw["email"], password=raw["password"]),
        browser_worker_builder=build_worker,
        browser_register_runner=run_worker,
    )

    result = BrowserRegistrationFlow(adapter).run(ctx)

    assert result.email == "user@example.com"
    assert ("build", "chatgpt") in events
    assert ("callback", "18885551234") in events
    assert ("cleanup", "chatgpt") in events


def test_phone_callback_does_not_inherit_platform_proxy(monkeypatch):
    captured = {}

    class FakeSettingsRepo:
        def get_default_provider_key(self, provider_type):
            return ""

        def resolve_runtime_settings(self, provider_type, provider_key, extra):
            return dict(extra)

    class FakeDefinitionsRepo:
        def get_by_key(self, provider_type, provider_key):
            return None

    def fake_create_phone_callbacks(provider_key, config, *, service, country="", log_fn=None):
        captured.update(
            {
                "provider_key": provider_key,
                "config": dict(config),
                "service": service,
                "country": country,
            }
        )
        return (lambda: "18885551234", lambda: None)

    monkeypatch.setattr(
        "infrastructure.provider_settings_repository.ProviderSettingsRepository",
        FakeSettingsRepo,
    )
    monkeypatch.setattr(
        "infrastructure.provider_definitions_repository.ProviderDefinitionsRepository",
        FakeDefinitionsRepo,
    )
    monkeypatch.setattr(helpers_module, "create_phone_callbacks", fake_create_phone_callbacks)

    ctx = RegistrationContext(
        platform_name="lingya_qq",
        platform_display_name="LingYaQQ",
        platform=SimpleNamespace(mailbox=None),
        identity=SimpleNamespace(email="", has_mailbox=False, identity_provider="phone"),
        config=RegisterConfig(
            proxy="socks5://127.0.0.1:1080",
            extra={
                "sms_provider": "haozhuma_api",
                "haozhuma_user": "user1",
                "haozhuma_password": "pass1",
                "haozhuma_sid": "1000",
            },
        ),
        email=None,
        password=None,
        log_fn=lambda message: None,
    )

    callback, cleanup = helpers_module.build_phone_callbacks(ctx, service="lingya_qq")

    assert callable(callback)
    assert callable(cleanup)
    assert captured["provider_key"] == "haozhuma_api"
    assert captured["service"] == "1000"
    assert "sms_proxy" not in captured["config"]
    assert "proxy" not in captured["config"]
