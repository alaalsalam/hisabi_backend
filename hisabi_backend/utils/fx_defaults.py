"""Default FX helpers for wallet-scoped seeding and lookup."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

import frappe
from frappe.utils import flt, get_datetime, now_datetime

from hisabi_backend.utils.sync_common import apply_common_sync_fields


# Baseline defaults are intentionally conservative and match frontend onboarding defaults.
# They are fallback seed values, not authoritative market feed prices.
DEFAULT_FX_RATES: Dict[str, float] = {
    "SAR_USD": 0.2666,
    "SAR_EUR": 0.2456,
    "SAR_YER": 66.6667,
    "SAR_AED": 0.9793,
    "SAR_KWD": 0.0819,
    "SAR_BHD": 0.1004,
    "SAR_OMR": 0.1026,
    "SAR_QAR": 0.9707,
    "SAR_EGP": 8.24,
    "SAR_JOD": 0.1887,
    "SAR_GBP": 0.2120,
    "SAR_TRY": 8.56,
    "SAR_INR": 22.2,
    "SAR_PKR": 74.0,
    "USD_SAR": 3.75,
    "EUR_SAR": 4.072,
    "YER_SAR": 0.015,
    "AED_SAR": 1.0211,
    "KWD_SAR": 12.21,
    "BHD_SAR": 9.96,
    "OMR_SAR": 9.75,
    "QAR_SAR": 1.0302,
    "EGP_SAR": 0.1214,
    "JOD_SAR": 5.3,
    "GBP_SAR": 4.717,
    "TRY_SAR": 0.1168,
    "INR_SAR": 0.045,
    "PKR_SAR": 0.0135,
}

USER_DEFINED_SOURCES = {"custom", "api"}


def _normalize_currency(value: Any) -> str:
    return str(value or "").strip().upper()


def _pair_key(base_currency: str, quote_currency: str) -> str:
    return f"{_normalize_currency(base_currency)}_{_normalize_currency(quote_currency)}"


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def parse_enabled_currencies(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_normalize_currency(v) for v in value if _normalize_currency(v)]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [_normalize_currency(v) for v in parsed if _normalize_currency(v)]
            except Exception:
                pass
        return [_normalize_currency(v) for v in raw.split(",") if _normalize_currency(v)]
    return []


def _dedupe_currencies(values: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        normalized = _normalize_currency(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def resolve_default_fx_rate(base_currency: str, quote_currency: str) -> Optional[float]:
    base = _normalize_currency(base_currency)
    quote = _normalize_currency(quote_currency)
    if not base or not quote:
        return None
    if base == quote:
        return 1.0

    direct = flt(DEFAULT_FX_RATES.get(_pair_key(base, quote)) or 0)
    if direct > 0:
        return direct

    reverse = flt(DEFAULT_FX_RATES.get(_pair_key(quote, base)) or 0)
    if reverse > 0:
        return 1.0 / reverse

    base_to_sar = None
    if base == "SAR":
        base_to_sar = 1.0
    else:
        direct_base_to_sar = flt(DEFAULT_FX_RATES.get(_pair_key(base, "SAR")) or 0)
        if direct_base_to_sar > 0:
            base_to_sar = direct_base_to_sar
        else:
            sar_to_base = flt(DEFAULT_FX_RATES.get(_pair_key("SAR", base)) or 0)
            if sar_to_base > 0:
                base_to_sar = 1.0 / sar_to_base

    sar_to_quote = None
    if quote == "SAR":
        sar_to_quote = 1.0
    else:
        direct_sar_to_quote = flt(DEFAULT_FX_RATES.get(_pair_key("SAR", quote)) or 0)
        if direct_sar_to_quote > 0:
            sar_to_quote = direct_sar_to_quote
        else:
            quote_to_sar = flt(DEFAULT_FX_RATES.get(_pair_key(quote, "SAR")) or 0)
            if quote_to_sar > 0:
                sar_to_quote = 1.0 / quote_to_sar

    if base_to_sar and sar_to_quote:
        bridged = flt(base_to_sar * sar_to_quote, 8)
        return bridged if bridged > 0 else None
    return None


def _build_currency_pool(base_currency: Optional[str], enabled_currencies: Any) -> List[str]:
    base = _normalize_currency(base_currency)
    enabled = parse_enabled_currencies(enabled_currencies)
    defaults = ["SAR", "USD", "YER"]
    return _dedupe_currencies([base, *enabled, *defaults])


def _latest_rows_by_pair(wallet_id: str, currencies: Sequence[str]) -> Dict[tuple[str, str], Dict[str, Any]]:
    if not currencies:
        return {}
    rows = frappe.get_all(
        "Hisabi FX Rate",
        filters={
            "wallet_id": wallet_id,
            "is_deleted": 0,
            "base_currency": ["in", list(currencies)],
            "quote_currency": ["in", list(currencies)],
        },
        fields=["name", "client_id", "user", "base_currency", "quote_currency", "rate", "source", "effective_date"],
        order_by="effective_date desc, server_modified desc, name desc",
        limit_page_length=0,
    )
    out: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (_normalize_currency(row.get("base_currency")), _normalize_currency(row.get("quote_currency")))
        if key in out:
            continue
        out[key] = row
    return out


def _default_client_id(wallet_id: str, base: str, quote: str) -> str:
    return f"fx-default-{wallet_id}-{base}-{quote}"


def seed_wallet_default_fx_rates(
    *,
    wallet_id: str,
    user: str,
    base_currency: Optional[str] = None,
    enabled_currencies: Any = None,
    overwrite_defaults: Any = False,
    effective_date: Any = None,
) -> Dict[str, Any]:
    pool = _build_currency_pool(base_currency, enabled_currencies)
    if len(pool) < 2:
        return {
            "seeded": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "unresolved": [],
            "currencies": pool,
            "overwritten_defaults": False,
        }

    overwrite = _boolish(overwrite_defaults)
    now_dt = now_datetime()
    effective_dt = get_datetime(effective_date) or now_dt
    latest = _latest_rows_by_pair(wallet_id, pool)

    inserted = 0
    updated = 0
    skipped = 0
    unresolved: List[str] = []

    for base in pool:
        for quote in pool:
            if base == quote:
                continue

            key = (base, quote)
            reverse_key = (quote, base)
            existing = latest.get(key)
            reverse_existing = latest.get(reverse_key)

            if existing and str(existing.get("source") or "").strip().lower() in USER_DEFINED_SOURCES:
                skipped += 1
                continue
            if reverse_existing and str(reverse_existing.get("source") or "").strip().lower() in USER_DEFINED_SOURCES:
                skipped += 1
                continue

            rate = resolve_default_fx_rate(base, quote)
            if not rate or flt(rate) <= 0:
                unresolved.append(f"{base}/{quote}")
                continue

            if existing and not overwrite:
                skipped += 1
                continue

            if existing:
                doc = frappe.get_doc("Hisabi FX Rate", existing["name"])
                # Never rewrite user-entered/api values through default seeding.
                if str(doc.source or "").strip().lower() in USER_DEFINED_SOURCES:
                    skipped += 1
                    continue
            else:
                doc = frappe.new_doc("Hisabi FX Rate")
                doc.client_id = _default_client_id(wallet_id, base, quote)
                doc.name = doc.client_id
                doc.flags.name_set = True

            doc.user = doc.user or user
            doc.wallet_id = wallet_id
            doc.base_currency = base
            doc.quote_currency = quote
            doc.rate = flt(rate, 8)
            doc.effective_date = effective_dt
            doc.source = "default"
            doc.last_updated = now_dt
            apply_common_sync_fields(doc, bump_version=True, mark_deleted=False)
            doc.save(ignore_permissions=True)

            if existing:
                updated += 1
            else:
                inserted += 1

    unresolved = sorted(set(unresolved))
    return {
        "seeded": inserted + updated,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "unresolved": unresolved[:50],
        "currencies": pool,
        "overwritten_defaults": overwrite,
    }
