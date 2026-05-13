from __future__ import annotations

import os

from application.browser_assist import BrowserAssistRegistry, browser_assist_registry, normalize_proxy_url
from application.tasks import create_register_task, list_task_events


def _lingya_cookie(name: str, value: str, *, domain: str = ".lingya.qq.com") -> dict:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "secure": True,
        "httpOnly": name.startswith("v_"),
        "sameSite": "no_restriction",
    }


def _browser_import_payload(**overrides) -> dict:
    payload = {
        "platform": "lingya_qq",
        "name": "Imported Lingya",
        "cookies": [
            _lingya_cookie("v_vusession", "session-1"),
            _lingya_cookie("v_vurefresh", "refresh-1"),
            _lingya_cookie("v_vuserid", "vuid-1"),
            _lingya_cookie("vdevice_guid", "device-1"),
            _lingya_cookie("nick", "%E6%B5%8B%E8%AF%95"),
            _lingya_cookie("last_refresh_vuserid", "vuid-1"),
        ],
        "user_agent": "Mozilla/5.0 Chrome/147.0.0.0",
        "sec_ch_ua": '"Chromium";v="147"',
        "sec_ch_ua_platform": '"Windows"',
        "proxy_url": "HTTP://127.0.0.1:80/",
        "max_concurrency": 3,
    }
    payload.update(overrides)
    return payload


def test_browser_assist_claim_matches_empty_proxy():
    registry = BrowserAssistRegistry()
    request = registry.publish_lingya_phone_login(
        task_id="task_1",
        phone="+8613800138000",
        local_phone="13800138000",
        area_code="+86",
        proxy_url="",
    )

    claimed = registry.claim(platform="lingya_qq", proxy_url="", extension_id="ext_1")

    assert claimed
    assert claimed["assist_id"] == request["assist_id"]
    assert claimed["local_phone"] == "13800138000"
    assert registry.claim(platform="lingya_qq", proxy_url="", extension_id="ext_2") is None


def test_browser_assist_claim_normalizes_proxy():
    registry = BrowserAssistRegistry()
    registry.publish_lingya_phone_login(
        task_id="task_1",
        phone="+8613800138000",
        local_phone="13800138000",
        area_code="+86",
        proxy_url="HTTP://127.0.0.1:80/",
    )

    claimed = registry.claim(platform="lingya_qq", proxy_url="http://127.0.0.1", extension_id="ext_1")

    assert claimed is not None
    assert normalize_proxy_url(claimed["proxy_url"]) == "http://127.0.0.1"


def test_browser_assist_api_claim_and_state_writes_task_event(client):
    browser_assist_registry.clear_for_tests()
    task = create_register_task({"platform": "lingya_qq", "count": 1})
    request = browser_assist_registry.publish_lingya_phone_login(
        task_id=task["id"],
        phone="+8613800138000",
        local_phone="13800138000",
        area_code="+86",
        proxy_url="",
    )

    claim_resp = client.post(
        "/api/browser/assist/claim",
        json={"extension_id": "ext_1", "platform": "lingya_qq", "proxy_url": ""},
    )
    state_resp = client.post(
        f"/api/browser/assist/{request['assist_id']}/state",
        json={"extension_id": "ext_1", "state": "filled", "detail": {"input_found": True}},
    )

    assert claim_resp.status_code == 200
    assert claim_resp.json()["request"]["assist_id"] == request["assist_id"]
    assert state_resp.status_code == 200
    events = list_task_events(task["id"], since=0, limit=20)
    assert any(item["type"] == "browser_assist" and "已自动填入手机号" in item["message"] for item in events)
    browser_assist_registry.clear_for_tests()


def test_browser_assist_api_claim_supports_get_without_auth_headers(client):
    browser_assist_registry.clear_for_tests()
    request = browser_assist_registry.publish_lingya_phone_login(
        task_id="task_get",
        phone="+8613800138000",
        local_phone="13800138000",
        area_code="+86",
        proxy_url="",
    )

    claim_resp = client.get(
        "/api/browser/assist/claim",
        params={"extension_id": "ext_get", "platform": "lingya_qq", "proxy_url": ""},
    )

    assert claim_resp.status_code == 200
    assert claim_resp.json()["request"]["assist_id"] == request["assist_id"]
    browser_assist_registry.clear_for_tests()


def test_browser_assist_api_uses_existing_bearer_auth_when_enabled(client, monkeypatch):
    browser_assist_registry.clear_for_tests()
    monkeypatch.setenv("APP_PASSWORD", "secret")

    unauthorized = client.post("/api/browser/assist/claim", json={"extension_id": "ext_1"})
    authorized = client.post(
        "/api/browser/assist/claim",
        headers={"Authorization": "Bearer secret"},
        json={"extension_id": "ext_1"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert os.environ.get("APP_PASSWORD") is None


def test_browser_import_account_creates_lingya_account(client):
    resp = client.post("/api/browser/import-account", json=_browser_import_payload())

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "created"
    assert data["account"]["platform"] == "lingya_qq"
    assert data["account"]["vuid"] == "vuid-1"
    assert "v_vusession" in data["cookies"]["names"]

    list_resp = client.get("/api/accounts", params={"platform": "lingya_qq"})
    item = list_resp.json()["items"][0]
    credentials = {
        row["key"]: row["value"]
        for row in item["credentials"]
        if row.get("scope") == "platform"
    }
    legacy_extra = item["overview"]["legacy_extra"]
    assert item["email"] == "Imported Lingya"
    assert item["primary_token"] == "session-1"
    assert credentials["v_vusession"] == "session-1"
    assert credentials["v_vurefresh"] == "refresh-1"
    assert credentials["last_refresh_vuserid"] == "vuid-1"
    assert "vdevice_guid=device-1" in credentials["cookies"]
    assert legacy_extra["proxy_url"] == "http://127.0.0.1"
    assert legacy_extra["user_agent"] == "Mozilla/5.0 Chrome/147.0.0.0"
    assert legacy_extra["lingya2api_max_concurrency"] == 3


def test_browser_import_account_updates_by_vuid(client):
    first = client.post("/api/browser/import-account", json=_browser_import_payload())
    account_id = first.json()["account"]["id"]
    client.patch(
        f"/api/accounts/{account_id}",
        json={"overview": {"legacy_extra": {"lingya_qq_publish_source_url": "https://example.com/work"}}},
    )
    updated_payload = _browser_import_payload(
        name="Renamed Lingya",
        cookies=[
            _lingya_cookie("v_vusession", "session-2"),
            _lingya_cookie("v_vurefresh", "refresh-2"),
            _lingya_cookie("v_vuserid", "vuid-1"),
            _lingya_cookie("vdevice_guid", "device-1"),
        ],
    )

    second = client.post("/api/browser/import-account", json=updated_payload)

    assert second.status_code == 200
    assert second.json()["action"] == "updated"
    assert second.json()["account"]["id"] == account_id
    list_resp = client.get("/api/accounts", params={"platform": "lingya_qq"})
    item = list_resp.json()["items"][0]
    credentials = {row["key"]: row["value"] for row in item["credentials"]}
    legacy_extra = item["overview"]["legacy_extra"]
    assert list_resp.json()["total"] == 1
    assert item["primary_token"] == "session-2"
    assert credentials["v_vurefresh"] == "refresh-2"
    assert legacy_extra["lingya_qq_publish_source_url"] == "https://example.com/work"
    assert legacy_extra["lingya2api_name"] == "Renamed Lingya"


def test_browser_import_account_rejects_incomplete_lingya_cookies(client):
    resp = client.post(
        "/api/browser/import-account",
        json=_browser_import_payload(
            cookies=[
                _lingya_cookie("v_vusession", "session-1"),
                _lingya_cookie("v_vuserid", "vuid-1"),
                _lingya_cookie("vdevice_guid", "device-1"),
            ]
        ),
    )

    assert resp.status_code == 400
    assert "v_vurefresh" in resp.json()["detail"]
