"""Account CRUD endpoint tests."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from application.account_exports import AccountExportsService
from domain.accounts import AccountCreateCommand, AccountExportSelection
from infrastructure.accounts_repository import AccountsRepository


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def _create_account(client, **overrides):
    payload = {
        "platform": "chatgpt",
        "email": "test@example.com",
        "password": "TestPass123!",
        **overrides,
    }
    return client.post("/api/accounts", json=payload)


def test_create_account(client):
    resp = _create_account(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform"] == "chatgpt"
    assert data["email"] == "test@example.com"
    assert "id" in data


def test_list_accounts_empty(client):
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_accounts_after_create(client):
    _create_account(client)
    resp = client.get("/api/accounts")
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["email"] == "test@example.com"


def test_list_accounts_supports_pagination(client):
    for index in range(3):
        _create_account(client, email=f"page-{index}@example.com")

    resp = client.get("/api/accounts", params={"platform": "chatgpt", "page": 2, "page_size": 1})
    data = resp.json()

    assert data["total"] == 3
    assert data["page"] == 2
    assert data["page_size"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["email"] == "page-1@example.com"


def test_get_account_by_id(client):
    create_resp = _create_account(client)
    account_id = create_resp.json()["id"]
    resp = client.get(f"/api/accounts/{account_id}")
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


def test_get_account_not_found(client):
    resp = client.get("/api/accounts/99999")
    assert resp.status_code == 404


def test_delete_account(client):
    create_resp = _create_account(client)
    account_id = create_resp.json()["id"]
    del_resp = client.delete(f"/api/accounts/{account_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True
    # Verify it's gone
    get_resp = client.get(f"/api/accounts/{account_id}")
    assert get_resp.status_code == 404


def test_downstream_batch_delete_accounts_is_platform_scoped(client):
    by_email = _create_account(
        client,
        platform="freebeat",
        email="delete-by-email@example.com",
        user_id="freebeat-email-user",
        credentials={"access_token": "secret"},
    ).json()
    by_id = _create_account(
        client,
        platform="freebeat",
        email="delete-by-id@example.com",
        user_id="freebeat-id-user",
    ).json()
    by_user_id = _create_account(
        client,
        platform="freebeat",
        email="delete-by-user@example.com",
        user_id="freebeat-user-id",
    ).json()
    other_platform = _create_account(
        client,
        platform="chatgpt",
        email="delete-by-email@example.com",
        user_id="freebeat-user-id",
    ).json()

    resp = client.post(
        "/api/accounts/platform/freebeat/batch-delete",
        json={
            "account_ids": [by_id["id"]],
            "emails": ["DELETE-BY-EMAIL@example.com", "missing@example.com"],
            "accounts": [{"user_id": "freebeat-user-id"}],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["platform"] == "freebeat"
    assert data["matched"] == 3
    assert data["deleted"] == 3
    assert {item["id"] for item in data["deleted_accounts"]} == {
        by_email["id"],
        by_id["id"],
        by_user_id["id"],
    }
    assert data["not_found"] == {
        "account_ids": [],
        "emails": ["missing@example.com"],
        "user_ids": [],
    }
    assert client.get(f"/api/accounts/{by_email['id']}").status_code == 404
    assert client.get(f"/api/accounts/{by_id['id']}").status_code == 404
    assert client.get(f"/api/accounts/{by_user_id['id']}").status_code == 404
    assert client.get(f"/api/accounts/{other_platform['id']}").status_code == 200


def test_downstream_batch_delete_rejects_empty_selector(client):
    account = _create_account(client, platform="freebeat", email="keep@example.com").json()

    resp = client.post("/api/accounts/platform/freebeat/batch-delete", json={})

    assert resp.status_code == 400
    assert "at least one" in resp.json()["detail"]
    assert client.get(f"/api/accounts/{account['id']}").status_code == 200


def test_downstream_batch_delete_accepts_bearer_app_password(client, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    account = _create_account(client, platform="freebeat", email="protected@example.com").json()
    monkeypatch.setenv("APP_PASSWORD", "callback-secret")
    body = {"accounts": [{"source_account_id": account["id"]}]}

    unauthorized = client.post("/api/accounts/platform/freebeat/batch-delete", json=body)
    authorized = client.post(
        "/api/accounts/platform/freebeat/batch-delete",
        json=body,
        headers={"Authorization": "Bearer callback-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["deleted"] == 1


def test_delete_platform_low_quota_accounts_uses_configurable_range(client):
    low = _create_account(
        client,
        platform="lingya_qq",
        email="low@example.com",
        overview={"quota_balance": 72},
    ).json()
    nested = _create_account(
        client,
        platform="lingya_qq",
        email="nested@example.com",
        overview={"quota": {"quota_balance": "1"}},
    ).json()
    zero = _create_account(
        client,
        platform="lingya_qq",
        email="zero@example.com",
        overview={"quota_balance": 0},
    ).json()
    boundary = _create_account(
        client,
        platform="lingya_qq",
        email="boundary@example.com",
        overview={"quota_balance": 73},
    ).json()
    other_platform = _create_account(
        client,
        platform="chatgpt",
        email="other@example.com",
        overview={"quota_balance": 10},
    ).json()

    resp = client.delete(
        "/api/accounts/platform/lingya_qq/low-quota",
        params={"min_exclusive": 0, "max_exclusive": 73},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted"] == 2
    assert data["platform"] == "lingya_qq"
    assert data["min_exclusive"] == 0
    assert data["max_exclusive"] == 73
    assert {item["id"] for item in data["deleted_accounts"]} == {low["id"], nested["id"]}
    assert client.get(f"/api/accounts/{low['id']}").status_code == 404
    assert client.get(f"/api/accounts/{nested['id']}").status_code == 404
    assert client.get(f"/api/accounts/{zero['id']}").status_code == 200
    assert client.get(f"/api/accounts/{boundary['id']}").status_code == 200
    assert client.get(f"/api/accounts/{other_platform['id']}").status_code == 200


def test_delete_freebeat_low_quota_accounts_uses_default_range(client):
    low = _create_account(
        client,
        platform="freebeat",
        email="freebeat-low@example.com",
        overview={"total_credits": 79},
    ).json()
    zero = _create_account(
        client,
        platform="freebeat",
        email="freebeat-zero@example.com",
        overview={"remaining_credits": "0"},
    ).json()
    nested = _create_account(
        client,
        platform="freebeat",
        email="freebeat-nested@example.com",
        overview={"credits": {"totalCredits": "50"}},
    ).json()
    negative = _create_account(
        client,
        platform="freebeat",
        email="freebeat-negative@example.com",
        overview={"total_credits": -1},
    ).json()
    boundary = _create_account(
        client,
        platform="freebeat",
        email="freebeat-boundary@example.com",
        overview={"total_credits": 80},
    ).json()

    resp = client.delete("/api/accounts/platform/freebeat/low-quota")

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 3
    assert data["min_exclusive"] == -1
    assert data["max_exclusive"] == 80
    assert {item["id"] for item in data["deleted_accounts"]} == {low["id"], zero["id"], nested["id"]}
    assert client.get(f"/api/accounts/{low['id']}").status_code == 404
    assert client.get(f"/api/accounts/{zero['id']}").status_code == 404
    assert client.get(f"/api/accounts/{nested['id']}").status_code == 404
    assert client.get(f"/api/accounts/{negative['id']}").status_code == 200
    assert client.get(f"/api/accounts/{boundary['id']}").status_code == 200


def test_low_quota_ranges_endpoint_exposes_backend_defaults_and_updates(client):
    defaults_resp = client.get("/api/accounts/low-quota-ranges")

    assert defaults_resp.status_code == 200
    defaults = defaults_resp.json()
    assert defaults["defaults"]["lingya_qq"] == {"min_exclusive": 0, "max_exclusive": 73}
    assert defaults["defaults"]["freebeat"] == {"min_exclusive": -1, "max_exclusive": 80}
    assert defaults["defaults"]["imgs_weryai"] == {"min_exclusive": 0, "max_exclusive": 1}
    assert defaults["fallback"] == {"min_exclusive": 0, "max_exclusive": 73}

    update_resp = client.put(
        "/api/accounts/platform/freebeat/low-quota-range",
        json={"min_exclusive": 10, "max_exclusive": 20},
    )

    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["configured"]["freebeat"] == {"min_exclusive": 10, "max_exclusive": 20}
    assert updated["effective"]["freebeat"] == {"min_exclusive": 10, "max_exclusive": 20}


def test_delete_imgs_weryai_low_quota_accounts_supports_decimal_balance(client):
    low = _create_account(
        client,
        platform="imgs_weryai",
        email="wery-low@example.com",
        overview={"remaining_credits": 0.3},
    ).json()
    nested_string = _create_account(
        client,
        platform="imgs_weryai",
        email="wery-string@example.com",
        overview={"credits": {"total_credits": "0.8"}},
    ).json()
    zero = _create_account(
        client,
        platform="imgs_weryai",
        email="wery-zero@example.com",
        overview={"remaining_credits": 0},
    ).json()
    boundary = _create_account(
        client,
        platform="imgs_weryai",
        email="wery-boundary@example.com",
        overview={"remaining_credits": 1},
    ).json()

    resp = client.delete("/api/accounts/platform/imgs_weryai/low-quota")

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 2
    assert data["min_exclusive"] == 0
    assert data["max_exclusive"] == 1
    assert {item["id"] for item in data["deleted_accounts"]} == {low["id"], nested_string["id"]}
    assert {item["quota_balance"] for item in data["deleted_accounts"]} == {0.3, 0.8}
    assert client.get(f"/api/accounts/{low['id']}").status_code == 404
    assert client.get(f"/api/accounts/{nested_string['id']}").status_code == 404
    assert client.get(f"/api/accounts/{zero['id']}").status_code == 200
    assert client.get(f"/api/accounts/{boundary['id']}").status_code == 200


def test_low_quota_range_update_accepts_decimal_values(client):
    update_resp = client.put(
        "/api/accounts/platform/imgs_weryai/low-quota-range",
        json={"min_exclusive": 0.1, "max_exclusive": 0.9},
    )

    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["configured"]["imgs_weryai"] == {"min_exclusive": 0.1, "max_exclusive": 0.9}
    assert data["effective"]["imgs_weryai"] == {"min_exclusive": 0.1, "max_exclusive": 0.9}


def test_delete_platform_low_quota_accounts_uses_saved_range(client):
    client.put(
        "/api/accounts/platform/freebeat/low-quota-range",
        json={"min_exclusive": 10, "max_exclusive": 20},
    )
    low = _create_account(
        client,
        platform="freebeat",
        email="saved-range-low@example.com",
        overview={"total_credits": 19},
    ).json()
    below = _create_account(
        client,
        platform="freebeat",
        email="saved-range-below@example.com",
        overview={"total_credits": 10},
    ).json()
    above = _create_account(
        client,
        platform="freebeat",
        email="saved-range-above@example.com",
        overview={"total_credits": 20},
    ).json()

    resp = client.delete("/api/accounts/platform/freebeat/low-quota")

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 1
    assert data["min_exclusive"] == 10
    assert data["max_exclusive"] == 20
    assert {item["id"] for item in data["deleted_accounts"]} == {low["id"]}
    assert client.get(f"/api/accounts/{low['id']}").status_code == 404
    assert client.get(f"/api/accounts/{below['id']}").status_code == 200
    assert client.get(f"/api/accounts/{above['id']}").status_code == 200


def test_update_account(client):
    create_resp = _create_account(client)
    account_id = create_resp.json()["id"]
    patch_resp = client.patch(
        f"/api/accounts/{account_id}",
        json={"password": "NewPass456!"},
    )
    assert patch_resp.status_code == 200


def test_filter_accounts_by_platform(client):
    _create_account(client, platform="chatgpt", email="a@test.com")
    _create_account(client, platform="cursor", email="b@test.com")
    resp = client.get("/api/accounts", params={"platform": "cursor"})
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["platform"] == "cursor"


def test_account_stats(client):
    _create_account(client)
    resp = client.get("/api/accounts/stats")
    assert resp.status_code == 200


def test_create_lingya_qq_account_expands_cookie_header(client):
    resp = _create_account(
        client,
        platform="lingya_qq",
        email="+8613800138000",
        password="",
        credentials={
            "cookies": (
                "v_vusession=session-cookie; v_vurefresh=refresh-cookie; "
                "v_vuserid=vuid-cookie; vdevice_guid=device-cookie"
            )
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    credentials = {
        item["key"]: item["value"]
        for item in data["credentials"]
        if item.get("scope") == "platform"
    }
    assert data["user_id"] == "vuid-cookie"
    assert data["primary_token"] == "session-cookie"
    assert credentials["vusession"] == "session-cookie"
    assert credentials["v_vusession"] == "session-cookie"
    assert credentials["v_vurefresh"] == "refresh-cookie"
    assert credentials["v_vuserid"] == "vuid-cookie"
    assert credentials["vdevice_guid"] == "device-cookie"
    assert "v_vusession=session-cookie" in credentials["cookies"]


def test_export_kiro_go(client):
    # Create a kiro account first
    client.post("/api/accounts", json={
        "platform": "kiro",
        "email": "kiro@test.com",
        "password": "",
    })
    resp = client.post("/api/accounts/export/kiro-go", json={
        "platform": "kiro",
        "select_all": True,
    })
    assert resp.status_code == 200
    assert "kiro_go_config" in resp.headers.get("content-disposition", "")


def test_export_any2api_multi_platform(client):
    client.post("/api/accounts", json={"platform": "kiro", "email": "k@test.com", "password": ""})
    client.post("/api/accounts", json={"platform": "grok", "email": "g@test.com", "password": ""})
    client.post("/api/accounts", json={"platform": "cursor", "email": "c@test.com", "password": ""})
    resp = client.post("/api/accounts/export/any2api", json={"select_all": True})
    assert resp.status_code == 200
    assert "any2api_admin" in resp.headers.get("content-disposition", "")


def test_export_cpa_uses_standard_payload_schema():
    exp_timestamp = 1777166030
    expected_expired = datetime.fromtimestamp(
        exp_timestamp, tz=timezone(timedelta(hours=8))
    ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    access_token = _make_jwt({
        "exp": exp_timestamp,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct-standard",
        },
    })
    id_token = _make_jwt({
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct-standard",
        },
    })
    repository = AccountsRepository()
    repository.create(
        AccountCreateCommand(
            platform="chatgpt",
            email="cpa@test.com",
            password="TestPass123!",
            user_id="acct-standard",
            credentials={
                "access_token": access_token,
                "refresh_token": "rt_standard",
                "id_token": id_token,
            },
        )
    )
    service = AccountExportsService(repository)

    artifact = service.export_chatgpt_cpa(AccountExportSelection(platform="chatgpt", select_all=True))
    payload = json.loads(artifact.content)
    assert list(payload.keys()) == [
        "access_token",
        "account_id",
        "email",
        "expired",
        "id_token",
        "last_refresh",
        "refresh_token",
        "type",
    ]
    assert payload["access_token"] == access_token
    assert payload["account_id"] == "acct-standard"
    assert payload["email"] == "cpa@test.com"
    assert payload["expired"] == expected_expired
    assert payload["id_token"] == id_token
    assert payload["last_refresh"].endswith("+08:00")
    assert payload["refresh_token"] == "rt_standard"
    assert payload["type"] == "codex"


def test_export_cpa_falls_back_to_stored_user_id_for_account_id():
    repository = AccountsRepository()
    repository.create(
        AccountCreateCommand(
            platform="chatgpt",
            email="fallback@test.com",
            password="TestPass123!",
            user_id="acct-from-user-id",
            credentials={
                "access_token": _make_jwt({"exp": 1777166030}),
                "refresh_token": "rt_fallback",
            },
        )
    )
    service = AccountExportsService(repository)

    artifact = service.export_chatgpt_cpa(AccountExportSelection(platform="chatgpt", select_all=True))
    payload = json.loads(artifact.content)
    assert payload["account_id"] == "acct-from-user-id"
    assert payload["refresh_token"] == "rt_fallback"
