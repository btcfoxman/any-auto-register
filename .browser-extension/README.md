# Any Register Lingya Browser Extension

This is a local Chrome/Edge Manifest V3 extension for importing the current Lingya browser cookie environment into any-auto-register and assisting manual Lingya phone-login tasks.

## Load

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable developer mode.
3. Click "Load unpacked".
4. Select this `browser-extension` directory.

## Use

1. Sign in at `https://lingya.qq.com/` in the same browser profile.
2. Open the extension popup.
3. Set `Service URL`, default `http://192.168.3.5:8000`; for another device on the same LAN, use `http://<LAN-IP>:8000`.
4. Leave `API Key / APP_PASSWORD` blank unless the backend was started with `APP_PASSWORD=...`; if it was, enter that exact value.
5. Click `Grant Access` if the popup shows that service access is required, then reload the extension after manifest changes.
6. Optionally set `Proxy URL`, for example `http://user:pwd@127.0.0.1:10809`.
7. Keep the extension loaded. The background task assistant polls the service and will claim Lingya registration tasks whose proxy matches this `Proxy URL`; two empty proxy values also match.
8. Click `Import Lingya Account` only when you want to import an already signed-in Lingya browser account into this project.

The task assistant sends `POST /api/browser/assist/claim` and reports state to `POST /api/browser/assist/{assist_id}/state`. If `API Key / APP_PASSWORD` is filled, it uses `Authorization: Bearer <value>` and also sends `X-API-Key` for compatibility.
When a matching task is claimed, the extension opens or focuses `https://lingya.qq.com/`, shows a flashing top-right panel with phone/proxy/task details, and fills the phone input. It does not click the SMS send button or solve the graphic CAPTCHA.

The cookie importer sends `POST /api/browser/import-account`; when `API Key / APP_PASSWORD` is filled it includes `Authorization: Bearer <value>` and `X-API-Key`.
When the popup opens it scans the Lingya cookies, shows the cookie names/domains/masked values that will be sent, and uses the `nick` cookie as the default account name when available. The backend creates or updates a local `lingya_qq` account and stores the cookie header, `vdevice_guid`, `vuserid`, refresh/session cookies, UA client hints, proxy URL, and Lingya2API concurrency metadata in the account graph. If `Proxy URL` is set, it is used by the service for that account's Lingya/COS requests.
The backend rejects incomplete imports that are missing `v_vusession`/`vusession`, `v_vurefresh`, `v_vuserid`/`vuserid`, or `vdevice_guid`, because those fields are required for later keepalive and refresh operations.

The synchronized cookie allowlist is derived from request-side `post_data_json.ext.cookies` entries in `tmp/fetch_xhr.ndjson`:

`_new_next_refresh_time`, `_qimei_fingerprint`, `_qimei_h38`, `_qimei_q36`, `_qimei_uuid42`, `avatar`, `env`, `last_refresh_second`, `last_refresh_time`, `last_refresh_vuserid`, `min_expire_time`, `nick`, `v_login_time_init`, `v_main_login`, `v_next_refresh_time`, `v_t_access_token`, `v_t_appid`, `v_t_openid`, `v_t_refresh_token`, `v_vurefresh`, `v_vuserid`, `v_vusession`, `vdevice_guid`, `video_appid`, `video_platform`, `vqq_vuserid`, `vqq_vusession`, `vuserid`, `vusession`.

Scanning uses URL queries, domain queries, and an `all-accessible` cookie-store fallback, then filters locally to the `qq.com` / `tencent.com` domain families and the allowlisted names above. If none match, the popup also shows raw cookie names read from the browser so the mismatch can be diagnosed without broadening synchronization beyond request-proven cookies.

When the active tab is `lingya.qq.com`, the popup also injects a short script to read `document.cookie`. This is the most reliable fallback for the non-HttpOnly `.lingya.qq.com` cookies shown in DevTools.

The default `http://192.168.3.5:8000` origin is listed in `host_permissions`. For other LAN hosts, the popup can grant the origin through `optional_host_permissions`; reload the extension if you edited `manifest.json`.
