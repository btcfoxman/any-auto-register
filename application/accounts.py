from __future__ import annotations

import ast
import csv
import json
import re
from decimal import Decimal, InvalidOperation
from typing import TypeAlias

from core.datetime_utils import serialize_datetime
from core.config_store import config_store
from domain.accounts import (
    AccountBatchDeleteCommand,
    AccountCreateCommand,
    AccountImportLine,
    AccountQuery,
    AccountRecord,
    AccountStats,
    AccountUpdateCommand,
)
from infrastructure.accounts_repository import AccountsRepository


IMPORT_LINE_RE = re.compile(
    r'^\s*(?P<email>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\S+)'
    r'\s+(?P<password>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\S+)'
    r'(?:\s+(?P<extra>.*))?\s*$'
)


QuotaNumber: TypeAlias = int | float


LOW_QUOTA_DELETE_DEFAULT_RANGES: dict[str, tuple[QuotaNumber, QuotaNumber]] = {
    "lingya_qq": (0, 73),
    "freebeat": (-1, 80),
    "imgs_weryai": (0, 1),
}
LOW_QUOTA_DELETE_CONFIG_KEY = "account_low_quota_delete_ranges"
FALLBACK_LOW_QUOTA_DELETE_RANGE = (0, 73)


def _parse_quota_number(value) -> Decimal:
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError("quota value is required")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("quota value must be numeric") from exc
    if not parsed.is_finite():
        raise ValueError("quota value must be finite")
    return parsed


def _json_quota_number(value: Decimal | QuotaNumber) -> QuotaNumber:
    parsed = value if isinstance(value, Decimal) else _parse_quota_number(value)
    if parsed == parsed.to_integral_value():
        return int(parsed)
    return float(parsed)


def _range_dict(min_exclusive: Decimal | QuotaNumber, max_exclusive: Decimal | QuotaNumber) -> dict[str, QuotaNumber]:
    return {
        "min_exclusive": _json_quota_number(min_exclusive),
        "max_exclusive": _json_quota_number(max_exclusive),
    }


def _default_low_quota_ranges() -> dict[str, dict[str, QuotaNumber]]:
    return {
        platform: _range_dict(min_value, max_value)
        for platform, (min_value, max_value) in LOW_QUOTA_DELETE_DEFAULT_RANGES.items()
    }


def _parse_low_quota_ranges(raw: str) -> dict[str, dict[str, QuotaNumber]]:
    try:
        data = json.loads(raw) if str(raw or "").strip() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return {}
    ranges: dict[str, dict[str, QuotaNumber]] = {}
    for platform, value in data.items():
        if not isinstance(value, dict):
            continue
        try:
            min_value = _parse_quota_number(value.get("min_exclusive"))
            max_value = _parse_quota_number(value.get("max_exclusive"))
        except (TypeError, ValueError):
            continue
        if max_value <= min_value:
            continue
        platform_key = str(platform or "").strip()
        if platform_key:
            ranges[platform_key] = _range_dict(min_value, max_value)
    return ranges


def _decode_import_token(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        try:
            decoded = ast.literal_eval(text)
            return decoded if isinstance(decoded, str) else str(decoded)
        except Exception:
            return text[1:-1]
    return text


def _parse_csv_row(raw: str) -> list[str]:
    return next(csv.reader([raw]))


class AccountsService:
    def __init__(self, repository: AccountsRepository | None = None):
        self.repository = repository or AccountsRepository()

    def list_accounts(self, query: AccountQuery) -> dict:
        total, items = self.repository.list(query)
        return {
            "total": total,
            "page": query.page,
            "page_size": query.page_size,
            "items": [self._serialize(item) for item in items],
        }

    def get_account(self, account_id: int) -> dict | None:
        item = self.repository.get(account_id)
        return self._serialize(item) if item else None

    def create_account(self, command: AccountCreateCommand) -> dict:
        return self._serialize(self.repository.create(command))

    def update_account(self, account_id: int, command: AccountUpdateCommand) -> dict | None:
        item = self.repository.update(account_id, command)
        return self._serialize(item) if item else None

    def delete_account(self, account_id: int) -> dict:
        return {"ok": self.repository.delete(account_id)}

    def batch_delete_accounts(self, command: AccountBatchDeleteCommand) -> dict:
        platform = str(command.platform or "").strip().lower()
        if not platform:
            raise ValueError("platform is required")

        account_ids = list(dict.fromkeys(int(value) for value in command.account_ids if int(value) > 0))
        emails = list(
            dict.fromkeys(
                text.lower()
                for value in command.emails
                if (text := str(value or "").strip())
            )
        )
        user_ids = list(
            dict.fromkeys(
                text
                for value in command.user_ids
                if (text := str(value or "").strip())
            )
        )
        if not account_ids and not emails and not user_ids:
            raise ValueError("at least one account_id, email, or user_id is required")
        return self.repository.delete_accounts_by_identifiers(
            platform,
            account_ids=account_ids,
            emails=emails,
            user_ids=user_ids,
        )

    def get_low_quota_delete_ranges(self) -> dict:
        defaults = _default_low_quota_ranges()
        fallback = _range_dict(*FALLBACK_LOW_QUOTA_DELETE_RANGE)
        configured = _parse_low_quota_ranges(config_store.get(LOW_QUOTA_DELETE_CONFIG_KEY, ""))
        effective = {**defaults, **configured}
        return {
            "ok": True,
            "config_key": LOW_QUOTA_DELETE_CONFIG_KEY,
            "defaults": defaults,
            "fallback": fallback,
            "configured": configured,
            "effective": effective,
        }

    def update_low_quota_delete_range(self, platform: str, *, min_exclusive: QuotaNumber, max_exclusive: QuotaNumber) -> dict:
        platform_key = str(platform or "").strip()
        if not platform_key:
            raise ValueError("platform is required")
        min_value = _parse_quota_number(min_exclusive)
        max_value = _parse_quota_number(max_exclusive)
        if max_value <= min_value:
            raise ValueError("max_exclusive must be greater than min_exclusive")
        configured = _parse_low_quota_ranges(config_store.get(LOW_QUOTA_DELETE_CONFIG_KEY, ""))
        configured[platform_key] = _range_dict(min_value, max_value)
        config_store.set(
            LOW_QUOTA_DELETE_CONFIG_KEY,
            json.dumps(configured, ensure_ascii=False, sort_keys=True),
        )
        return self.get_low_quota_delete_ranges()

    def delete_low_quota_accounts(
        self,
        platform: str,
        *,
        min_exclusive: QuotaNumber | None = None,
        max_exclusive: QuotaNumber | None = None,
    ) -> dict:
        platform_key = str(platform or "").strip()
        if not platform_key:
            raise ValueError("platform is required")
        ranges = self.get_low_quota_delete_ranges()
        default_range = ranges["effective"].get(platform_key) or ranges["fallback"]
        default_min = _parse_quota_number(default_range["min_exclusive"])
        default_max = _parse_quota_number(default_range["max_exclusive"])
        min_value = default_min if min_exclusive is None else _parse_quota_number(min_exclusive)
        max_value = default_max if max_exclusive is None else _parse_quota_number(max_exclusive)
        if max_value <= min_value:
            raise ValueError("max_exclusive must be greater than min_exclusive")
        return self.repository.delete_accounts_by_quota_range(
            platform_key,
            min_exclusive=_json_quota_number(min_value),
            max_exclusive=_json_quota_number(max_value),
        )

    def import_accounts(self, platform: str, lines: list[str]) -> dict:
        parsed: list[AccountImportLine] = []
        csv_header: list[str] | None = None
        for line in lines:
            raw = line.strip()
            if not raw:
                continue
            if csv_header is None and "," in raw:
                try:
                    header_candidate = [item.strip().lower() for item in _parse_csv_row(raw)]
                except Exception:
                    header_candidate = []
                if "email" in header_candidate and "password" in header_candidate:
                    csv_header = header_candidate
                    continue
            if csv_header is not None:
                try:
                    values = _parse_csv_row(raw)
                except Exception:
                    values = []
                if values:
                    row = {
                        csv_header[index]: values[index]
                        for index in range(min(len(csv_header), len(values)))
                    }
                    email = str(row.get("email", "") or "").strip()
                    password = str(row.get("password", "") or "")
                    if email and password and "@" in email and " " not in email:
                        extra = {}
                        cashier_url = str(row.get("cashier_url", "") or "").strip()
                        if cashier_url:
                            extra["cashier_url"] = cashier_url
                        parsed.append(AccountImportLine(email=email, password=password, extra=extra))
                        continue
            match = IMPORT_LINE_RE.match(raw)
            if not match:
                continue
            email = _decode_import_token(match.group("email"))
            password = _decode_import_token(match.group("password"))
            extra = {}
            payload = (match.group("extra") or "").strip()
            if payload:
                try:
                    decoded = json.loads(payload)
                    if isinstance(decoded, dict):
                        extra = decoded
                    elif decoded not in (None, ""):
                        extra = {"cashier_url": str(decoded)}
                except Exception:
                    extra = {"cashier_url": _decode_import_token(payload)}
            parsed.append(AccountImportLine(email=email, password=password, extra=extra))
        return {"created": self.repository.import_lines(platform, parsed)}

    def export_csv(self, query: AccountQuery) -> str:
        return self.repository.export_csv(query)

    def get_stats(self) -> dict:
        stats: AccountStats = self.repository.stats()
        return {
            "total": stats.total,
            "by_platform": stats.by_platform,
            "by_status": stats.by_status,
            "by_lifecycle_status": stats.by_lifecycle_status,
            "by_plan_state": stats.by_plan_state,
            "by_validity_status": stats.by_validity_status,
            "by_display_status": stats.by_display_status,
        }

    @staticmethod
    def _serialize(item: AccountRecord) -> dict:
        return {
            "id": item.id,
            "platform": item.platform,
            "email": item.email,
            "password": item.password,
            "user_id": item.user_id,
            "primary_token": item.primary_token,
            "trial_end_time": item.trial_end_time,
            "cashier_url": item.cashier_url,
            "lifecycle_status": item.lifecycle_status,
            "validity_status": item.validity_status,
            "plan_state": item.plan_state,
            "plan_name": item.plan_name,
            "display_status": item.display_status,
            "overview": item.overview,
            "display_summary": item.display_summary,
            "credentials": item.credentials,
            "provider_accounts": item.provider_accounts,
            "provider_resources": item.provider_resources,
            "created_at": serialize_datetime(item.created_at),
            "updated_at": serialize_datetime(item.updated_at),
        }
