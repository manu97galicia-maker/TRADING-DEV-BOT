"""Offline retry policy and synthetic socket failures; no network or trading."""

# ruff: noqa: ANN201, ARG001

import asyncio
import json
import logging
import unittest
from collections import deque
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from monitoring.pressure_trial_source import PressureTrialSource
from monitoring.stream_reconnect import (
    http_status,
    reconnect_delay,
    retry_after_seconds,
)

NOW = 1_800_000_000.0
SECRET = "https://private.invalid/ws?api-key=DO_NOT_PRINT_ME\nPRIVATE_RESPONSE_BODY"  # noqa: S105 -- synthetic leak-detection fixture


def modern_error(status: int, retry_after: str | None = None) -> InvalidStatus:
    headers = Headers({"Retry-After": retry_after}) if retry_after is not None else Headers()
    return InvalidStatus(Response(status, "Private reason " + SECRET, headers, body=SECRET.encode()))


class RetryPolicyTests(unittest.TestCase):
    def test_modern_and_both_legacy_status_locations(self):
        self.assertEqual(reconnect_delay(modern_error(429), 1), (429, 60))
        for attribute in ("status", "status_code"):
            error = RuntimeError(SECRET)
            error.response = SimpleNamespace(**{attribute: 429})
            self.assertEqual(http_status(error), 429)
        error = RuntimeError(SECRET)
        error.status_code = 429
        error.headers = {"Retry-After": "120"}
        self.assertEqual(reconnect_delay(error, 1), (429, 120))

    def test_legacy_http_429_text_is_a_fallback_only(self):
        for message in ("server rejected WebSocket connection: HTTP 429 " + SECRET,
                        "HTTP/1.1 429 Too Many Requests " + SECRET):
            self.assertEqual(reconnect_delay(RuntimeError(message), 1), (429, 60))
        explicit = modern_error(503)
        explicit.args = ("HTTP 429 " + SECRET,)
        self.assertEqual(reconnect_delay(explicit, 1), (503, 2))
        self.assertIsNone(http_status(RuntimeError("request 429 " + SECRET)))

    def test_backoff_is_exponential_and_bounded_for_repeated_failures(self):
        expected = [2, 4, 8, 16, 32, 64, 128, 256, 300, 300, 300]
        self.assertEqual([reconnect_delay(OSError(SECRET), count)[1]
                          for count in range(1, 12)], expected)
        self.assertEqual(reconnect_delay(modern_error(429), 1)[1], 60)
        self.assertEqual(reconnect_delay(modern_error(429), 9)[1], 300)
        self.assertEqual(reconnect_delay(OSError(SECRET), 10**100)[1], 300)

    def test_retry_after_seconds_can_extend_but_cannot_shorten_the_cooldown(self):
        self.assertEqual(reconnect_delay(modern_error(429, "10"), 1), (429, 60))
        self.assertEqual(reconnect_delay(modern_error(429, " 120 "), 1), (429, 120))
        self.assertEqual(reconnect_delay(modern_error(503, "600"), 20), (503, 600))

    def test_valid_http_dates_and_past_dates(self):
        future = datetime.fromtimestamp(NOW + 120, tz=timezone.utc)
        for value in (format_datetime(future, usegmt=True),
                      future.strftime("%A, %d-%b-%y %H:%M:%S GMT"),
                      future.strftime("%a %b %d %H:%M:%S %Y")):
            with self.subTest(value=value):
                self.assertEqual(reconnect_delay(modern_error(429, value), 1, now=NOW), (429, 120))
        past = format_datetime(datetime.fromtimestamp(NOW - 1, tz=timezone.utc), usegmt=True)
        self.assertEqual(reconnect_delay(modern_error(503, past), 1, now=NOW), (503, 2))

    def test_malformed_nonfinite_duplicate_or_injected_headers_are_ignored(self):
        future = format_datetime(datetime.fromtimestamp(NOW + 120, tz=timezone.utc), usegmt=True)
        for value in ("", "NaN", "nan", "inf", "Infinity", "-1", "1.5", "1e500", "9" * 10000,
                      "tomorrow", future + " trailing secret", future + "\r\n" + SECRET,
                      "Wed, 99 Nov 99999 00:00:00 GMT", "2027-01-01T00:00:00Z"):
            with self.subTest(value=value[:80]):
                self.assertIsNone(retry_after_seconds(modern_error(429, value), now=NOW))
                self.assertEqual(reconnect_delay(modern_error(429, value), 1, now=NOW), (429, 60))
        duplicated = modern_error(429)
        duplicated.response.headers["Retry-After"] = "120"
        duplicated.response.headers["Retry-After"] = "180"
        self.assertEqual(reconnect_delay(duplicated, 1), (429, 60))
        self.assertIsNone(retry_after_seconds(modern_error(429, future), now=float("nan")))
        self.assertIsNone(retry_after_seconds(modern_error(429, future), now=float("inf")))


class ScriptedSocket:
    def __init__(self, frames: list[dict]) -> None:
        self.frames = deque(frames)
        self.send = AsyncMock()

    async def recv(self) -> str:
        if not self.frames:
            raise ConnectionError(SECRET)
        return json.dumps(self.frames.popleft())


class ScriptedAttempt:
    def __init__(self, attempt: Exception | ScriptedSocket) -> None:
        self.attempt = attempt

    async def __aenter__(self) -> ScriptedSocket:
        if isinstance(self.attempt, Exception):
            raise self.attempt
        return self.attempt

    async def __aexit__(self, *args: object) -> bool:
        return False


def log_data(*, err: object = None) -> dict:
    return {"method": "logsNotification", "params": {"result": {
        "context": {"slot": 100}, "value": {"signature": "synthetic", "err": err, "logs": []}}}}


class CollectorReconnectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.observer = SimpleNamespace(on_slot=Mock(), on_logs=Mock(), disconnect=Mock(), close=AsyncMock())
        self.client = Mock()
        self.source = PressureTrialSource("wss://" + SECRET, object(), self.client, Mock(), Mock(), Mock(),
                                          event_observer=self.observer, observer_only=True, tracked_mints=set)

    async def run_attempts(self, attempts: list[Exception | ScriptedSocket]) -> list[float]:
        pending = deque(attempts)
        delays = []

        def connect(*args: object, **kwargs: object) -> ScriptedAttempt:
            if not pending:
                raise asyncio.CancelledError
            return ScriptedAttempt(pending.popleft())

        async def sleep(delay: float) -> None:
            delays.append(delay)

        with (patch("monitoring.pressure_trial_source.websockets.connect", side_effect=connect),
              patch("monitoring.pressure_trial_source.asyncio.sleep", side_effect=sleep),
              patch("sqlite3.connect", side_effect=AssertionError("No SQL during reconnect")),
              self.assertRaises(asyncio.CancelledError)):
            await self.source.run(AsyncMock())
        self.assertEqual(self.client.mock_calls, [])
        return delays

    async def test_rate_limit_cooldown_and_safe_single_line_warning(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        logging.disable(logging.NOTSET)
        with self.assertLogs("monitoring.pressure_trial_source", level="WARNING") as captured:
            delays = await self.run_attempts([modern_error(429, "120")])
        self.assertEqual(delays, [120])
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertTrue(record.getMessage().startswith("Pressure stream interrupted"))
        self.assertIn("InvalidStatus, HTTP 429", record.getMessage())
        self.assertIn("120.0 s", record.getMessage())
        self.assertNotIn("\n", record.getMessage())
        self.assertNotIn("DO_NOT_PRINT_ME", record.getMessage())
        self.assertNotIn("PRIVATE_RESPONSE_BODY", record.getMessage())
        self.assertNotIn("https://", record.getMessage())
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.stack_info)

    async def test_open_and_subscription_acks_do_not_reset_flapping_backoff(self):
        delays = await self.run_attempts([
            OSError(SECRET), ScriptedSocket([{"result": 1}]),
            ScriptedSocket([{"result": 1}, {"id": 2, "result": 2}]),
        ])
        self.assertEqual(delays, [2, 4, 8])

    async def test_valid_logs_reset_failures_but_malformed_subscription_data_does_not(self):
        malformed = log_data()
        malformed["params"]["result"]["value"]["logs"] = "not a list"
        delays = await self.run_attempts([
            OSError(SECRET), OSError(SECRET), ScriptedSocket([{"result": 1}, log_data()]),
            ScriptedSocket([{"result": 1}, malformed]),
        ])
        self.assertEqual(delays, [2, 4, 2, 4])

    async def test_valid_slot_subscription_data_resets_failures(self):
        delays = await self.run_attempts([
            OSError(SECRET), OSError(SECRET), ScriptedSocket([
                {"result": 1}, {"id": 2, "result": 2},
                {"method": "slotNotification", "params": {"result": {"slot": 101, "parent": 100}}},
            ]),
        ])
        self.assertEqual(delays, [2, 4, 2])
        self.observer.on_slot.assert_called_once_with({"slot": 101, "parent": 100})

    async def test_valid_failed_transaction_data_also_proves_stream_delivery(self):
        delays = await self.run_attempts([
            OSError(SECRET), OSError(SECRET),
            ScriptedSocket([{"result": 1}, log_data(err={"InstructionError": [0, "Custom"]})]),
        ])
        self.assertEqual(delays, [2, 4, 2])

    async def test_observer_disconnect_precedes_backoff_and_cancellation_closes_tasks(self):
        order = []
        self.observer.disconnect.side_effect = lambda: order.append("disconnect")
        self.observer.close.side_effect = lambda: order.append("close")
        task = asyncio.create_task(asyncio.Event().wait())
        self.source.tasks.add(task)
        await asyncio.sleep(0)

        async def cancel_sleep(delay: float) -> None:
            order.append(("sleep", delay))
            raise asyncio.CancelledError

        with (patch("monitoring.pressure_trial_source.websockets.connect", side_effect=modern_error(429)) as connect,
              patch("monitoring.pressure_trial_source.asyncio.sleep", side_effect=cancel_sleep),
              self.assertRaises(asyncio.CancelledError)):
            await self.source.run(AsyncMock())
        self.assertEqual(order, ["disconnect", ("sleep", 60), "close"])
        connect.assert_called_once()
        self.observer.close.assert_awaited_once()
        self.assertTrue(task.cancelled())

    async def test_cancellation_before_connect_never_sleeps_and_closes_observer(self):
        with (patch("monitoring.pressure_trial_source.websockets.connect", side_effect=asyncio.CancelledError),
              patch("monitoring.pressure_trial_source.asyncio.sleep") as sleep,
              self.assertRaises(asyncio.CancelledError)):
            await self.source.run(AsyncMock())
        sleep.assert_not_called()
        self.observer.disconnect.assert_not_called()
        self.observer.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
