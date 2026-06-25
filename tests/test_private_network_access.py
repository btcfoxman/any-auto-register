from __future__ import annotations


def test_private_network_access_preflight_for_extension(client):
    resp = client.options(
        "/api/browser/assist/claim",
        headers={
            "Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-api-key",
            "Access-Control-Request-Private-Network": "true",
        },
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert resp.headers["access-control-allow-private-network"] == "true"
    assert "authorization" in resp.headers["access-control-allow-headers"]
