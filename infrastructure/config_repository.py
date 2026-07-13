from __future__ import annotations

from core.config_store import config_store
from infrastructure.provider_definitions_repository import ProviderDefinitionsRepository


class ConfigRepository:
    BASE_KEYS = {
        "default_executor",
        "default_identity_provider", "default_oauth_provider", "oauth_email_hint",
        "chrome_user_data_dir", "chrome_cdp_url",
        "cpa_api_url", "cpa_api_key",
        "team_manager_url", "team_manager_key",
        "any2api_url", "any2api_password",
        "lingya2api_url", "lingya2api_api_key", "lingya2api_max_concurrency",
        "lingya_qq_keepalive_enabled", "lingya_qq_heartbeat_interval_seconds", "lingya_qq_balance_interval_seconds",
        "lingya_qq_keepalive_concurrency",
        "lingya_qq_keepalive_retire_enabled", "lingya_qq_keepalive_retire_quota_threshold",
        "lingya_qq_keepalive_retire_after_hours",
        "lingya_qq_auto_daily_sign_in", "lingya_qq_auto_publish_after_register", "lingya_qq_publish_required",
        "lingya_qq_publish_source_url", "lingya_qq_publish_cover_url",
        "lingya_qq_publish_creation_process_text",
        "lingya_qq_publish_credit_timeout", "lingya_qq_publish_credit_poll_interval",
        "lingya_qq_publish_source_timeout", "lingya_qq_publish_generation_timeout",
        "lingya_qq_publish_generation_poll_interval", "lingya_qq_publish_highlight_fallback_delay",
        "lingya_qq_publish_initial_delay",
        "lingya_qq_publish_poll_interval", "lingya_qq_publish_timeout",
        "lingya_qq_publish_post_quota_delay",
        "freebeat_daily_sign_in_enabled", "freebeat_daily_sign_in_min_interval_seconds",
        "freebeat_daily_sign_in_max_interval_seconds", "freebeat_auto_daily_sign_in",
        "freebeat_retire_low_credit_enabled", "freebeat_retire_credit_threshold",
        "freebeat_retire_after_hours",
        "freebeat_auto_questionnaire",
        "freebeat_send_code_browser_enabled", "freebeat_send_code_browser_headless",
        "freebeat_send_code_browser_required", "freebeat_send_code_browser_timeout_seconds",
        "freebeat_send_code_browser_cdp_url", "freebeat_send_code_browser_cdp_launcher_url",
        "freebeat_send_code_browser_user_agent",
        "freebeat_send_code_turnstile_click_enabled",
        "freebeat2api_url", "freebeat2api_api_key", "freebeat2api_max_concurrency",
        "freebeat2api_enable_auto_maintenance",
        "quickframe_keepalive_enabled", "quickframe_heartbeat_interval_seconds",
        "quickframe2api_url", "quickframe2api_api_key", "quickframe2api_max_concurrency",
        "quickframe2api_enable_auto_maintenance",
        "imgs_weryai_keepalive_enabled", "imgs_weryai_heartbeat_interval_seconds",
        "imgs_weryai_daily_sign_in_enabled", "imgs_weryai_daily_sign_in_day",
        "imgs2api_url", "imgs2api_api_key", "imgs2api_max_concurrency",
        "imgs2api_auto_sync_after_register", "imgs2api_enable_auto_maintenance", "imgs2api_proxy_host_override",
        "account_low_quota_delete_ranges",
    }

    def __init__(self, definitions: ProviderDefinitionsRepository | None = None):
        self.definitions = definitions or ProviderDefinitionsRepository()

    def get_allowed_keys(self) -> set[str]:
        keys = set(self.BASE_KEYS)
        for provider_type in ("mailbox", "captcha", "sms"):
            for definition in self.definitions.list_by_type(provider_type, enabled_only=False):
                for field in definition.get_fields():
                    field_key = str(field.get("key") or "").strip()
                    if field_key:
                        keys.add(field_key)
        return keys

    def get_flat(self) -> dict[str, str]:
        data = config_store.get_all()
        allowed = self.get_allowed_keys()
        return {
            key: str(value or "")
            for key, value in data.items()
            if key in allowed
        }

    def update_flat(self, data: dict[str, str]) -> list[str]:
        allowed = self.get_allowed_keys()
        safe = {key: value for key, value in data.items() if key in allowed}
        config_store.set_many(safe)
        return list(safe.keys())
