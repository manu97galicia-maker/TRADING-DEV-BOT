"""Pure retry policy for the read-only Pump WebSocket collector."""

from __future__ import annotations

import math
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

BASE_DELAY_SECONDS = 2
MAX_BACKOFF_SECONDS = 300
RATE_LIMIT_DELAY_SECONDS = 60
_MAX_EXPONENT = 8
_HTTP_RATE_LIMIT = 429
_LOWEST_HTTP_STATUS = 100
_HIGHEST_HTTP_STATUS = 599
_HTTP_STATUS_DIGITS = 3
_LEGACY_RATE_LIMIT = re.compile(r"\bHTTP(?:/\d(?:\.\d)?)?[ \t]+429\b", re.IGNORECASE)
_HTTP_DATE = re.compile(
    r"(?:[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT"
    r"|[A-Za-z]+, \d{2}-[A-Za-z]{3}-\d{2} \d{2}:\d{2}:\d{2} GMT"
    r"|[A-Za-z]{3} [A-Za-z]{3} [ \d]\d \d{2}:\d{2}:\d{2} \d{4})", re.IGNORECASE)


def http_status(error: Exception) -> int | None:
    """Read modern/legacy status fields; never return exception text."""
    response = getattr(error, "response", None)
    for value in (getattr(response, "status_code", None), getattr(response, "status", None),
                  getattr(error, "status_code", None)):
        if isinstance(value, int) and not isinstance(value, bool):
            status = int(value)
        elif type(value) is str and value.isascii() and value.isdecimal():
            if len(value) != _HTTP_STATUS_DIGITS:
                continue
            status = int(value)
        else:
            continue
        if _LOWEST_HTTP_STATUS <= status <= _HIGHEST_HTTP_STATUS:
            return status
    return _HTTP_RATE_LIMIT if _LEGACY_RATE_LIMIT.search(str(error)) else None


def _retry_after_header(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(error, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    try:
        value = getter("Retry-After")
        if value is None:
            value = getter("retry-after")
    except (LookupError, TypeError, ValueError):
        return None
    if type(value) is not str or any(character in value for character in "\r\n"):
        return None
    return value.strip()


def retry_after_seconds(error: Exception, *, now: float | None = None) -> float | None:
    """Accept finite nonnegative delta-seconds or an HTTP date in UTC."""
    value = _retry_after_header(error)
    if value is None:
        return None
    if value.isascii() and value.isdecimal():
        delay = float(value)
        return delay if math.isfinite(delay) else None
    if not _HTTP_DATE.fullmatch(value):
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current = time.time() if now is None else now
        delay = parsed.timestamp() - current
        if not math.isfinite(current) or not math.isfinite(delay):
            return None
        return max(0.0, delay)
    except (OverflowError, TypeError, ValueError):
        return None


def reconnect_delay(error: Exception, failures: int, *, now: float | None = None) -> tuple[int | None, float]:
    """Bound exponential retries, then honor a longer provider cooldown."""
    exponent = min(max(failures - 1, 0), _MAX_EXPONENT)
    delay = float(min(MAX_BACKOFF_SECONDS, BASE_DELAY_SECONDS * 2**exponent))
    status = http_status(error)
    if status == _HTTP_RATE_LIMIT:
        delay = max(delay, RATE_LIMIT_DELAY_SECONDS)
    retry_after = retry_after_seconds(error, now=now)
    return status, max(delay, retry_after) if retry_after is not None else delay


def valid_subscription_data(data: object, *, allow_slots: bool) -> bool:
    """An ACK/open socket isn't proof that a subscription delivers data."""
    if type(data) is not dict or type(data.get("params")) is not dict:
        return False
    result = data["params"].get("result")
    if type(result) is not dict:
        return False
    if data.get("method") == "slotNotification" and allow_slots:
        slot, parent = result.get("slot"), result.get("parent")
        return type(slot) is int and type(parent) is int and 0 <= parent < slot
    if data.get("method") != "logsNotification":
        return False
    value, context = result.get("value"), result.get("context")
    return bool(type(value) is dict and type(context) is dict
                and type(context.get("slot")) is int and context["slot"] >= 0
                and type(value.get("signature")) is str and value["signature"]
                and type(value.get("logs")) is list
                and all(type(line) is str for line in value["logs"]))
