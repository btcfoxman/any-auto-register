"""MailCenterPoolMailbox register into unified registry."""
from core.base_mailbox import MailCenterPoolMailbox  # noqa: F401
from providers.registry import register_provider

register_provider("mailbox", "mail_center_pool_api")(MailCenterPoolMailbox)
