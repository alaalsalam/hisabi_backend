"""Simple rate-limiting helper (Redis preferred, in-memory fallback).

This is intended for high-risk endpoints like login/register/revoke.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import frappe
from frappe import _


_LOCAL_BUCKETS: dict[str, tuple[int, float]] = {}


@dataclass(frozen=True)
class RateLimitConfig:
	limit: int
	window_seconds: int


def rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
	"""Apply a rate limit.

	Raises frappe.TooManyRequestsError (HTTP 429) on limit exceed.
	"""
	if not key:
		return

	cache = frappe.cache()
	redis_key = cache.make_key(f"hisabi_rl:{key}")

	# Redis path (atomic enough for our use). Falls back on connection errors.
	try:
		count = cache.incr(redis_key)  # type: ignore[arg-type]
		if count == 1:
			cache.expire(redis_key, window_seconds)  # type: ignore[arg-type]
		if count > int(limit):
			frappe.throw(_("rate_limited"), frappe.TooManyRequestsError)
		return
	except Exception:
		# local fallback
		pass

	now = time.time()
	count, start = _LOCAL_BUCKETS.get(key, (0, now))
	if now - start > window_seconds:
		count, start = 0, now
	count += 1
	_LOCAL_BUCKETS[key] = (count, start)
	if count > int(limit):
		frappe.throw(_("rate_limited"), frappe.TooManyRequestsError)

