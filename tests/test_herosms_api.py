from __future__ import annotations


def test_config_options_include_herosms_provider(client):
    resp = client.get("/api/config/options")
    assert resp.status_code == 200
    data = resp.json()
    providers = data["sms_providers"]
    hero = next(item for item in providers if item["value"] in {"herosms", "herosms_api"})
    assert hero["label"] == "HeroSMS"
    assert any(field["key"] == "herosms_api_key" for field in hero["fields"])


def test_config_options_include_uomsg_provider(client):
    resp = client.get("/api/config/options")
    assert resp.status_code == 200
    data = resp.json()
    providers = data["sms_providers"]
    uomsg = next(item for item in providers if item["value"] == "uomsg_api")
    assert uomsg["label"] == "UOMsg"
    assert any(field["key"] == "uomsg_token" for field in uomsg["fields"])
    assert any(field["key"] == "uomsg_keyword" for field in uomsg["fields"])


def test_config_options_include_eomsg_provider(client):
    resp = client.get("/api/config/options")
    assert resp.status_code == 200
    data = resp.json()
    providers = data["sms_providers"]
    eomsg = next(item for item in providers if item["value"] == "eomsg_api")
    assert eomsg["label"] == "EOMsg"
    assert any(field["key"] == "eomsg_token" for field in eomsg["fields"])
    assert any(field["key"] == "eomsg_keyword" for field in eomsg["fields"])


def test_config_options_include_haozhuma_provider(client):
    resp = client.get("/api/config/options")
    assert resp.status_code == 200
    data = resp.json()
    providers = data["sms_providers"]
    haozhuma = next(item for item in providers if item["value"] == "haozhuma_api")
    assert haozhuma["label"] == "HaoZhuMa"
    assert not any(field["key"] == "haozhuma_token" for field in haozhuma["fields"])
    assert any(field["key"] == "haozhuma_user" for field in haozhuma["fields"])
    assert any(field["key"] == "haozhuma_password" for field in haozhuma["fields"])
    assert any(field["key"] == "haozhuma_sid" for field in haozhuma["fields"])
    assert any(field["key"] == "haozhuma_batch_size" for field in haozhuma["fields"])


def test_config_options_include_feihumsg_provider(client):
    resp = client.get("/api/config/options")
    assert resp.status_code == 200
    data = resp.json()
    providers = data["sms_providers"]
    feihumsg = next(item for item in providers if item["value"] == "feihumsg_api")
    assert feihumsg["label"] == "FeiHuMsg"
    assert any(field["key"] == "feihumsg_user" for field in feihumsg["fields"])
    assert any(field["key"] == "feihumsg_password" for field in feihumsg["fields"])
    assert any(field["key"] == "feihumsg_pid" for field in feihumsg["fields"])


def test_herosms_balance_endpoint_accepts_inline_api_key(client, monkeypatch):
    monkeypatch.setattr("core.base_sms.HeroSmsProvider.get_balance", lambda self: 12.345)

    resp = client.post("/api/sms/herosms/balance", json={"api_key": "hero123"})

    assert resp.status_code == 200
    assert resp.json() == {"balance": 12.345}


def test_herosms_balance_endpoint_requires_api_key(client):
    resp = client.post("/api/sms/herosms/balance", json={})

    assert resp.status_code == 400
    assert "HeroSMS API Key" in resp.json()["detail"]


def test_uomsg_balance_endpoint_accepts_inline_token(client, monkeypatch):
    monkeypatch.setattr("core.base_sms.UOMsgProvider.get_balance", lambda self: 8.5)

    resp = client.post("/api/sms/uomsg/balance", json={"token": "tok123"})

    assert resp.status_code == 200
    assert resp.json() == {"balance": 8.5}


def test_uomsg_balance_endpoint_requires_token(client):
    resp = client.post("/api/sms/uomsg/balance", json={})

    assert resp.status_code == 400
    assert "UOMsg API Token" in resp.json()["detail"]


def test_eomsg_balance_endpoint_accepts_inline_token(client, monkeypatch):
    monkeypatch.setattr("core.base_sms.EOMsgProvider.get_balance", lambda self: 9.5)

    resp = client.post("/api/sms/eomsg/balance", json={"token": "tok123"})

    assert resp.status_code == 200
    assert resp.json() == {"balance": 9.5}


def test_eomsg_balance_endpoint_requires_token(client):
    resp = client.post("/api/sms/eomsg/balance", json={})

    assert resp.status_code == 400
    assert "EOMsg API Token" in resp.json()["detail"]


def test_haozhuma_balance_endpoint_accepts_inline_credentials(client, monkeypatch):
    monkeypatch.setattr("core.base_sms.HaoZhuMaProvider.get_balance", lambda self: 18.5)

    resp = client.post(
        "/api/sms/haozhuma/balance",
        json={"user": "user1", "password": "pass1", "sid": "1000"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"balance": 18.5}


def test_haozhuma_balance_endpoint_requires_auth(client):
    resp = client.post("/api/sms/haozhuma/balance", json={})

    assert resp.status_code == 400
    assert "HaoZhuMa API 账号密码" in resp.json()["detail"]


def test_feihumsg_balance_endpoint_accepts_inline_credentials(client, monkeypatch):
    monkeypatch.setattr("core.base_sms.FeiHuMsgProvider.get_balance", lambda self: 28.5)

    resp = client.post(
        "/api/sms/feihumsg/balance",
        json={"user": "user1", "password": "pass1", "pid": "1001"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"balance": 28.5}


def test_feihumsg_balance_endpoint_requires_auth(client):
    resp = client.post("/api/sms/feihumsg/balance", json={})

    assert resp.status_code == 400
    assert "FeiHuMsg API 账号密码" in resp.json()["detail"]
