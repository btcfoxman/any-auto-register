"""HaoZhuMa provider registration."""
from core.base_sms import HaoZhuMaProvider  # noqa: F401
from providers.registry import register_provider

register_provider("sms", "haozhuma_api")(HaoZhuMaProvider)
