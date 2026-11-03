"""Accept Pump events only from successful, complete runtime invocations."""

from __future__ import annotations

import base64
import re

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
INVOKE = re.compile(r"^Program ([1-9A-HJ-NP-Za-km-z]+) invoke \[(\d+)\]$")
RETURN = re.compile(r"^Program ([1-9A-HJ-NP-Za-km-z]+) (success|failed:.*)$")


class IncompletePumpLogsError(ValueError):
    """Historical denominators must not silently omit unreadable events."""


def pump_events(logs: list[str], *, strict: bool = False) -> list[bytes]:
    try:
        return successful_events(logs)
    except (ValueError, TypeError):
        if strict:
            raise IncompletePumpLogsError("Incomplete Pump execution logs") from None  # noqa: TRY003
        return []


def successful_events(logs: list[str]) -> list[bytes]:  # noqa: C901 -- validate the invocation stack and discard rolled-back event frames
    if not isinstance(logs, list) or not all(isinstance(line, str) for line in logs):
        raise ValueError
    stack, committed = [], []
    for line in logs:
        if "log truncated" in line.lower():
            raise ValueError
        invoked, returned = INVOKE.fullmatch(line), RETURN.fullmatch(line)
        if invoked:
            if int(invoked[2]) != len(stack)+1:
                raise ValueError
            stack.append((invoked[1], []))
        elif returned:
            if not stack or stack[-1][0] != returned[1]:
                raise ValueError
            _program, events = stack.pop()
            if returned[2] == "success":
                (stack[-1][1] if stack else committed).extend(events)
        elif line.startswith("Program data: ") and stack and stack[-1][0] == PUMP_PROGRAM:
            stack[-1][1].append(base64.b64decode(line[14:], validate=True))
    if stack:
        raise ValueError
    return committed
