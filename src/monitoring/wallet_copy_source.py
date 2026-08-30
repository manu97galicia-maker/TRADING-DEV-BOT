"""Copy-trading source (on-chain, no paid API).

Follows a set of wallets and copies their pump.fun bonding-curve BUYS. Also
follows the money: when a watched wallet SENDS SOL to another wallet (a funding
transfer above a threshold), that destination is added to the watch set — so
freshly-funded wallets are copied the moment they buy. pump.fun only.

DOS CAMINOS, Y EL QUE MANDA ES EL WEBSOCKET
-------------------------------------------
1. `logsSubscribe` con `mentions: [wallet]` (si hay `wss_endpoint`): el nodo
   EMPUJA los logs de cada transaccion que mencione a la wallet en cuanto se
   procesa, y la compra se lee del propio `TradeEvent` que ya viene dentro
   (mint, sol, es_compra, quien). Cero peticiones HTTP para enterarse.
2. Sondeo de `getSignaturesForAddress` + `getTransaction` (el de siempre).
   Se queda como RED DE SEGURIDAD, mas lento: cubre los segundos de una
   reconexion del websocket y es lo unico que ve las TRANSFERENCIAS de SOL,
   que es de donde sale `follow_funded` (el websocket solo trae los logs del
   programa de pump.fun, no los envios de SOL).

Los dos alimentan el mismo deduplicador (`wallet:mint`), asi que una compra
vista por los dos caminos se copia UNA vez.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable

import aiohttp
import websockets

from interfaces.core import TokenInfo
from monitoring.volumen import eventos_en_logs
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
        wss_endpoint: str = "",
        poll_interval: float = 2.0,
        follow_funded: bool = True,
        min_funding_sol: float = 0.05,
        max_discovered: int = 40,
    ) -> None:
        self._watched: set[str] = {w.strip() for w in wallets if w and w.strip()}
        self._seed_wallets = set(self._watched)
        self._impls = platform_impls
        self._client = client
        self._rpc = rpc_endpoint
        self._wss = (wss_endpoint or "").strip()
        self._poll = poll_interval
        #: Cadencia del sondeo HTTP. Con el websocket puesto, sondear cada
        #: decima no aporta nada -- la compra ya llego por push -- y solo gasta
        #: peticiones, asi que el respaldo se espacia a 2 s como minimo.
        self._respaldo = max(poll_interval, 2.0) if (wss_endpoint or "").strip() else poll_interval
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

    async def _copiar(
        self,
        wallet: str,
        mint: str,
        token_callback: Callable[[TokenInfo], Awaitable[None]],
        via: str,
    ) -> None:
        """Reconstruye el TokenInfo de esa moneda y la manda a comprar.

        Se lanza como tarea aparte a proposito: reconstruir el TokenInfo son
        varias llamadas al RPC, y bloquear con ellas el bucle que escucha
        significaria enterarse tarde de la SIGUIENTE compra.
        """
        clave = f"{wallet}:{mint}"
        if clave in self._seen_key:
            return
        self._seen_key.add(clave)
        try:
            ti = await build_pumpfun_token_info(mint, self._impls, self._client)
        except Exception:  # una moneda ilegible no puede matar la fuente
            logger.exception("COPY: no pude reconstruir %s", mint[:8])
            return
        if ti is None:
            return
        logger.info(
            "COPY (%s): wallet %s compro %s -> copiamos",
            via, wallet[:6], ti.symbol or mint[:8],
        )
        await token_callback(ti)

    def _atender_notificacion(
        self, crudo: str, token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        """Saca de una notificacion del websocket las compras que hay que copiar."""
        datos = json.loads(crudo)
        if datos.get("method") != "logsNotification":
            return
        valor = datos.get("params", {}).get("result", {}).get("value", {})
        if valor.get("err"):
            return
        sig = valor.get("signature")
        if sig:
            # Para que el sondeo de respaldo no vuelva a mirar esta misma firma.
            self._seen_sig.add(sig)
        for mint, _sol, es_compra, quien in eventos_en_logs(valor.get("logs") or []):
            # `quien` es el comprador que firma el TradeEvent. Sin comprobarlo se
            # copiaria a cualquiera que aparezca en la misma transaccion que la
            # wallet vigilada.
            if not es_compra or quien not in self._watched:
                continue
            asyncio.create_task(  # noqa: RUF006
                self._copiar(quien, mint, token_callback, "ws")
            )

    async def _ws_loop(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Escucha por websocket las compras de las wallets vigiladas.

        `logsSubscribe` con `mentions` admite UNA direccion por suscripcion, asi
        que se abre una por wallet sobre la misma conexion. Se reconecta sola: si
        el socket se cae, el sondeo de respaldo cubre el hueco.
        """
        while True:
            try:
                async with websockets.connect(
                    self._wss, ping_interval=20, ping_timeout=20
                ) as ws:
                    suscritas: set[str] = set()
                    logger.info("Copy source: websocket conectado")
                    while True:
                        # Las wallets descubiertas por `follow_funded` aparecen
                        # sobre la marcha, asi que la lista se revisa en cada
                        # vuelta en vez de una sola vez al conectar.
                        for w in list(self._watched - suscritas):
                            await ws.send(json.dumps({
                                "jsonrpc": "2.0",
                                "id": len(suscritas) + 1,
                                "method": "logsSubscribe",
                                "params": [
                                    {"mentions": [w]},
                                    {"commitment": "processed"},
                                ],
                            }))
                            suscritas.add(w)
                            logger.info("Copy source: suscrito a %s", w[:6])
                        try:
                            crudo = await asyncio.wait_for(ws.recv(), timeout=5)
                        except TimeoutError:
                            continue  # sin trafico: se mira si hay wallets nuevas
                        self._atender_notificacion(crudo, token_callback)
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosed:
                # Cierre normal del nodo: pasa cada pocas horas y no es un fallo.
                # Sin esta rama se registraba con traceback y parecia un error.
                logger.warning("Copy source: websocket cerrado; reconecto en 3s")
                await asyncio.sleep(3)
            except Exception:  # reconectar es la respuesta a todo aqui
                logger.exception("Copy source WS caido; reconecto en 3s")
                await asyncio.sleep(3)

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        if not self._watched:
            logger.warning("Copy source: sin wallets configuradas")
            return
        logger.info(
            "Copy source ACTIVO (on-chain): %d wallets, ws=%s, sondeo %.1fs, "
            "follow_funded=%s (>= %.2f SOL); solo compras pump.fun",
            len(self._watched), "si" if self._wss else "no", self._respaldo,
            self._follow_funded, self._min_funding_lamports / 1e9,
        )
        ws_task = (
            asyncio.create_task(self._ws_loop(token_callback)) if self._wss else None
        )
        try:
            await self._poll_loop(token_callback)
        finally:
            if ws_task:
                ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ws_task

    async def _poll_loop(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Sondeo HTTP: red de seguridad del websocket y unica via de `follow_funded`."""
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
                            if mint:
                                await self._copiar(w, mint, token_callback, "sondeo")
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
                    await asyncio.sleep(self._respaldo)
                except asyncio.CancelledError:
                    logger.info("Copy source cancelado")
                    return
                except Exception:  # noqa: BLE001
                    logger.exception("Copy source error; sigue en 5s")
                    await asyncio.sleep(5)
