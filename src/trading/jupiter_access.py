"""Official Jupiter API access and conservative spacing for keyless requests."""

import asyncio
import os
from weakref import WeakKeyDictionary

API_BASE = "https://api.jup.ag"
_loops: WeakKeyDictionary = WeakKeyDictionary()


def jupiter_headers() -> dict[str, str]:
    """Return credentials only for callers targeting the official Jupiter host."""
    key = os.getenv("JUPITER_API_KEY", "").strip()
    return {"x-api-key": key} if key else {}


async def wait_jupiter_slot() -> None:
    """Space keyless calls at the documented 0.5 requests per second."""
    if jupiter_headers():
        return
    loop = asyncio.get_running_loop()
    state = _loops.setdefault(loop, {"lock": asyncio.Lock(), "last": None})
    async with state["lock"]:
        if state["last"] is not None:
            delay = 2.05 - (loop.time() - state["last"])
            if delay > 0:
                await asyncio.sleep(delay)
        state["last"] = loop.time()
