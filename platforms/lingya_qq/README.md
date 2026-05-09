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
- `lingya_qq_sms_timeout`: defaults to `600`
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
- `lingya_qq_publish_source_retries`: defaults to `3`
- `lingya_qq_publish_initial_delay`: defaults to `600`
- `lingya_qq_publish_poll_interval`: defaults to `60`
- `lingya_qq_publish_timeout`: defaults to `7200`

The publish source may return JSON such as:

```json
{
  "title": "example title",
  "description": "",
  "video_url": "https://example.com/video.mp4",
  "cover_url": "https://example.com/cover.jpg",
  "duration": 16,
  "cover_ratio": 0.75
}
```

`video_base64` and `cover_base64` are also accepted. If the source returns raw
video bytes directly, configure `lingya_qq_publish_cover_url`.

`lingya_qq_auto_send_sms` is disabled by default because Lingya requires the
human CAPTCHA step before SMS delivery in current testing.
