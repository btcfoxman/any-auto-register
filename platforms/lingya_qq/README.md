# LingYaQQ (Lingya)

LingYaQQ uses Tencent's browser risk control before SMS delivery. Direct
Playwright/HTTP SMS sending is not reliable, so this integration is explicitly
manual-assisted.

## Flow

1. The task rents a phone number from the configured SMS provider.
2. The task log shows the phone number.
3. A human opens `https://lingya.qq.com` in normal Chrome/Edge, enters the phone
   number, completes the graphic CAPTCHA, and clicks send SMS.
4. The task waits for the SMS provider to return the verification code.
5. The task calls `WebLogin` with phone + SMS code.
6. The account stores the Lingya cookie allowlist plus canonical
   `vuid`, `vusession`, `vurefresh`, and `vdevice_guid` fields.
7. Validity checks use Lingya business APIs such as `GetUserQuota` and
   `Space/Hello`.
8. The `keepalive_sync` action runs `Space/Hello`, refreshes the session with
   `WebRefresh` when the cookie schedule is due, and syncs the latest cookie
   header to a configured lingya2api instance.
9. Optional post-register automation can run the daily credits sign-in, publish
   one work from a third-party GET source, wait for the released status, refresh
   quota, and then sync the updated account state.

## Platform Capabilities

- Platform key: `lingya_qq`
- Executor: `manual_assisted` (also accepts `protocol` for backend
  compatibility)
- Identity mode: `manual_phone`
- SMS service default: `qq`
- Default area code: `+86`

## Runtime Notes

Lingya account storage is cookie-first. The current allowlist is the 28 cookie
names observed from the browser extension snapshot, including `v_vusession`,
`vusession`, `vqq_vusession`, `v_vurefresh`, `v_vuserid`, `vuserid`,
`vqq_vuserid`, and `vdevice_guid`. `WebLogin` responses are normalized into the
same fields so protocol login and browser-cookie import share one storage shape.

Useful extra fields:

- `lingya_qq_area_code`: defaults to `+86`
- `lingya_qq_sms_timeout`: defaults to `300`
- `lingya_qq_sms_service`: overrides SMS provider service code
- `lingya_qq_http_timeout`: defaults to `20`
- `lingya_qq_auto_send_sms`: experimental, defaults to disabled
- `lingya2api_url`: optional lingya2api service URL
- `lingya2api_api_key`: optional API key for `x-api-key`
- `lingya2api_max_concurrency`: pushed to lingya2api account metadata
- `lingya_qq_auto_daily_sign_in`: defaults to enabled
- `lingya_qq_auto_publish_after_register`: defaults to enabled when
  `lingya_qq_publish_source_url` is set
- `lingya_qq_publish_required`: defaults to disabled; when enabled a publish
  failure fails the registration task
- `lingya_qq_publish_source_url`: third-party GET API returning work material
- `lingya_qq_publish_cover_url`: fallback cover URL for raw video sources
- `lingya_qq_publish_creation_process_text`: defaults to `Seedance 2.0 全能参考`
- `lingya_qq_publish_source_timeout`: defaults to `60`
- `lingya_qq_publish_source_retries`: defaults to `3`
- `lingya_qq_video_upload_service_id`: defaults to
  `1000226_20250923195211_7dda2b6b`, the configured video SDK serviceId
  observed in the upload HAR
- `lingya_qq_publish_initial_delay`: defaults to `600`
- `lingya_qq_publish_poll_interval`: defaults to `60`
- `lingya_qq_publish_timeout`: defaults to `7200`
- `lingya_qq_publish_credit_timeout`: defaults to `1800`, waits for the first-post 500-credit grant
- `lingya_qq_publish_credit_poll_interval`: defaults to `30`

The third-party publish source URL is fetched by direct connection. Account
proxy settings are still used for Lingya account requests and uploads, but they
are not applied to the external material source.
When the source returns JSON, content fields are taken from that response only;
stored fallback content settings are not applied. `creation_process_text` falls
back to `Seedance 2.0 全能参考` when the source omits it. `lingya_qq_publish_cover_url`
is only used when the source itself returns raw video bytes.

The publish source may return JSON such as:

```json
{
  "title": "example title",
  "intro": "example intro",
  "prompt": "first highlight scene prompt",
  "creation_process_text": "Seedance 2.0 全能参考",
  "video_url": "https://example.com/video.mp4",
  "cover_url": "https://example.com/cover.jpg",
  "tag_infos": [{"id": "tag_2QCVIf1DjL", "title": "玄幻", "alias": ""}]
}
```

`video_base64` and `cover_base64` are also accepted. If the source returns raw
video bytes directly, configure `lingya_qq_publish_cover_url`.
`creationProcessText`, `creation_process`, `creationProcess`, `process_text`,
and `processText` are accepted as aliases for `creation_process_text`.

`lingya_qq_auto_send_sms` is disabled by default because Lingya requires the
human CAPTCHA step before SMS delivery in current testing.
