"""Copy-trading source (on-chain polling, no paid API).

Follows a set of wallets and copies their pump.fun bonding-curve BUYS. Also
follows the money: when a watched wallet SENDS SOL to another wallet (a funding
transfer above a threshold), that destination is added to the watch set — so
freshly-funded wallets are copied the moment they buy. pump.fun only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp

from interfaces.core import TokenInfo
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)

_PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_SYS_PROGRAM = "11111111111111111111111111111111"


class WalletCopySource:
    """Copy pump.fun buys of watched wallets; grow the set via funding transfers."""

    def __init__(
        self,
        wallets: list[str],
        platform_impls: object,
        client: object,
        rpc_endpoint: str = "https://api.mainnet-beta.solana.com",
        poll_interval: float = 2.0,
        follow_funded: bool = True,
        min_funding_sol: float = 0.05,
        max_discovered: int = 40,
        wallets_file: str | None = None,
    ) -> None:
        configured = {w.strip() for w in wallets if w and w.strip()}
        if wallets_file:
            path = Path(wallets_file)
            if path.exists():
                configured.update(
                    line.strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                )
            else:
                logger.warning("Copy source: no existe wallets_file=%s", path)
        self._watched: set[str] = configured
        self._seed_wallets = set(self._watched)
        self._impls = platform_impls
        self._client = client
        self._rpc = rpc_endpoint
        self._poll = poll_interval
        self._follow_funded = follow_funded
        self._min_funding_lamports = int(min_funding_sol * 1e9)
        self._max_discovered = max_discovered
        self._discovered: set[str] = set()
        self._seeded: set[str] = set()
        self._seen_sig: set[str] = set()
        self._seen_key: set[str] = set()

    async def _rpc_call(self, session, method, params):
        try:
            async with session.post(
                self._rpc,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            ) as r:
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
        except Exception:  # noqa: BLE001
            return None

    def _extract_bought_mint(self, tx: dict, wallet: str) -> str | None:
        meta = tx.get("meta") or {}
        if meta.get("err"):
            return None
        keys = [a["pubkey"] if isinstance(a, dict) else a
                for a in tx.get("transaction", {}).get("message", {}).get("accountKeys", [])]
        if _PUMP_PROGRAM not in keys:
            return None
        try:
            i = keys.index(wallet)
            if meta["postBalances"][i] - meta["preBalances"][i] >= 0:
                return None  # SOL didn't decrease -> not a buy
        except (ValueError, KeyError, IndexError):
            pass
        pre = {(b.get("owner"), b.get("mint")): float(b["uiTokenAmount"]["uiAmount"] or 0)
               for b in (meta.get("preTokenBalances") or [])}
        for b in (meta.get("postTokenBalances") or []):
            if b.get("owner") != wallet:
                continue
            mint = b.get("mint")
            if float(b["uiTokenAmount"]["uiAmount"] or 0) > pre.get((wallet, mint), 0.0):
                return mint
        return None

    def _extract_funded(self, tx: dict, wallet: str) -> list[str]:
        """Wallets this wallet SENT >= min_funding SOL to (System transfers)."""
        out: list[str] = []
        msg = tx.get("transaction", {}).get("message", {})
        ixs = list(msg.get("instructions", []))
        for inner in (tx.get("meta", {}).get("innerInstructions") or []):
            ixs.extend(inner.get("instructions", []))
        for ix in ixs:
            p = ix.get("parsed")
            if not isinstance(p, dict):
                continue
            if p.get("type") not in ("transfer", "transferChecked"):
                continue
            info = p.get("info", {})
            if info.get("source") != wallet:
                continue
            lam = int(info.get("lamports") or 0)
            dest = info.get("destination")
            if dest and dest != wallet and lam >= self._min_funding_lamports:
                out.append(dest)
        return out

    async def _seed(self, session, wallet):
        r = await self._rpc_call(session, "getSignaturesForAddress", [wallet, {"limit": 5}])
        for s in ((r or {}).get("result") or []):
            self._seen_sig.add(s["signature"])
        self._seeded.add(wallet)

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        if not self._watched:
            logger.warning("Copy source: sin wallets configuradas")
            return
        logger.info(
            "Copy source ACTIVO (on-chain): %d wallets, poll %.0fs, follow_funded=%s "
            "(>= %.2f SOL); solo compras pump.fun",
            len(self._watched), self._poll, self._follow_funded,
            self._min_funding_lamports / 1e9,
        )
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            for w in list(self._watched):
                await self._seed(session, w)
                await asyncio.sleep(0.2)

            while True:
                try:
                    for w in list(self._watched):
                        if w not in self._seeded:
                            await self._seed(session, w)
                            continue
                        r = await self._rpc_call(
                            session, "getSignaturesForAddress", [w, {"limit": 10}]
                        )
                        sigs = [s["signature"] for s in ((r or {}).get("result") or [])]
                        for sig in [s for s in reversed(sigs) if s not in self._seen_sig]:
                            self._seen_sig.add(sig)
                            tx = (await self._rpc_call(
                                session, "getTransaction",
                                [sig, {"maxSupportedTransactionVersion": 0,
                                       "encoding": "jsonParsed"}])
                                or {}).get("result")
                            if not tx:
                                continue
                            # 1) copy a pump.fun buy
                            mint = self._extract_bought_mint(tx, w)
                            if mint and f"{w}:{mint}" not in self._seen_key:
                                self._seen_key.add(f"{w}:{mint}")
                                ti = await build_pumpfun_token_info(
                                    mint, self._impls, self._client)
                                if ti is not None:
                                    logger.info("COPY: wallet %s compro %s -> copiamos",
                                                w[:6], ti.symbol or mint[:8])
                                    await token_callback(ti)
                            # 2) follow the money
                            if self._follow_funded and len(self._discovered) < self._max_discovered:
                                for dest in self._extract_funded(tx, w):
                                    if dest not in self._watched:
                                        self._watched.add(dest)
                                        self._discovered.add(dest)
                                        logger.info(
                                            "FUNDING: %s fondeo a %s -> ahora la vigilo "
                                            "(descubiertas: %d)",
                                            w[:6], dest[:6], len(self._discovered))
                        await asyncio.sleep(0.3)
                    await asyncio.sleep(self._poll)
                except asyncio.CancelledError:
                    logger.info("Copy source cancelado")
                    return
                except Exception:  # noqa: BLE001
                    logger.exception("Copy source error; sigue en 5s")
                    await asyncio.sleep(5)
