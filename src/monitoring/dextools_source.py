"""Fuente de compra: monedas que ENTRAN en el DEXTboard de DEXTools.

Por que hotpools y no un endpoint de pagos
------------------------------------------
No hay endpoint de pagos. Un pack NITRO (200 pts / 199 $ hasta 5000 pts /
3.999 $, 24 h) compra posicion en el DEXTboard, y el DEXTboard es lo que
devuelve ``/ranking/{chain}/hotpools``. Entrar en esa lista no es el recibo
del pago, es su efecto -- y como senal tiene una ventaja sobre el recibo:
llega ya validada por el mercado.

La senal es la ENTRADA, no la permanencia. Estar en el ranking es un estado
que dura horas; entrar es un suceso. Igual que en la fuente de DexScreener,
la foto del arranque se registra sin comprar para que el bot no se despierte
comprando las veinte que ya estaban.

El rango decide el importe: subir mas alto en el DEXTboard cuesta mas
NITRO, asi que la posicion es lo mas parecido a "cuanto se ha gastado" que
esta API deja leer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

from dextools.client import DextoolsClient, DextoolsHotPool
from interfaces.core import TokenInfo
from trading.token_builder import build_generic_token_info, build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)


def build_dextools_heartbeat(  # noqa: PLR0913
    poll_interval: float,
    entrantes: int,
    comprables: int,
    plan: str,
    *,
    configured: bool,
    paper_mode: bool,
    embudo: dict[str, int] | None = None,
) -> str:
    """Mensaje periodico de salud de la fuente de DEXTools."""
    if not configured:
        return (
            "🧪 <b>DEXTOOLS SIN CLAVE</b>\n"
            "Falta DEXTOOLS_API_KEY en el .env: la fuente no emite señales."
        )
    mode = "PAPER" if paper_mode else "REAL"
    lineas = [
        "🧪 <b>DEXTOOLS DEXTBOARD</b>",
        f"Ranking revisado · cada {poll_interval:g}s · plan {plan}",
        f"👀 Entradas nuevas: <b>{entrantes}</b>",
    ]
    if embudo:
        lineas.extend(
            [
                f"  ↳ no se pudo construir: {embudo.get('sin_construir', 0)}",
                f"  ↳ sin liquidez bloqueada: {embudo.get('sin_bloqueo', 0)}",
            ]
        )
    lineas.extend(
        [
            f"✅ Comprables: <b>{comprables}</b>",
            f"Modo: <b>{mode}</b>",
        ]
    )
    return "\n".join(lineas)


def importe_por_rango(
    rank: int | None,
    normal: float,
    rango_promocionado: int,
    promocionado: float,
) -> float:
    """Devuelve cuanto comprar segun lo alto que haya entrado en el ranking.

    Subir en el DEXTboard cuesta NITRO, asi que la posicion es el unico
    indicador de gasto que la API deja leer. Sin rango se usa el importe
    normal: no saber cuanto pagaron no es motivo para apostar mas.
    """
    if rank is None or rango_promocionado <= 0:
        return normal
    return promocionado if rank <= rango_promocionado else normal


class DexBoardEntrantTracker:
    """Acepta un mint una sola vez: la primera que aparece tras el arranque."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def prime(self, pools: list[DextoolsHotPool]) -> None:
        """Registra la foto del arranque sin tratarla como senal."""
        for pool in pools:
            self._seen.add(pool.mint)

    def accept(self, pool: DextoolsHotPool) -> bool:
        """True solo la primera vez que se ofrece este mint."""
        if not pool.mint or pool.mint in self._seen:
            return False
        self._seen.add(pool.mint)
        return True


class DextoolsHotPoolsSource:
    """Sondea el DEXTboard y emite TokenInfos para comprar."""

    def __init__(  # noqa: PLR0913
        self,
        platform_impls: object,
        client: object,
        *,
        poll_interval: float = 15.0,
        limit: int = 30,
        normal_buy_amount: float = 0.15,
        promoted_rank: int = 10,
        promoted_buy_amount: float = 0.20,
        heartbeat_interval: float = 300.0,
        paper_mode: bool = True,
        incluir_migradas: bool = True,
        incluir_cualquier_origen: bool = False,
        min_lp_locked_pct: float = 50.0,
        dextools: DextoolsClient | None = None,
    ) -> None:
        self._impls = platform_impls
        self._client = client
        self._dextools = dextools or DextoolsClient()
        self.poll_interval = poll_interval
        self.limit = limit
        self.normal_buy_amount = normal_buy_amount
        self.promoted_rank = promoted_rank
        self.promoted_buy_amount = promoted_buy_amount
        self.heartbeat_interval = heartbeat_interval
        self.paper_mode = paper_mode
        self.incluir_migradas = incluir_migradas
        self.incluir_cualquier_origen = incluir_cualquier_origen
        self.min_lp_locked_pct = min_lp_locked_pct
        self._tracker = DexBoardEntrantTracker()
        self._primed = False
        self._last_heartbeat = 0.0
        self._entrantes = 0
        self._comprables = 0
        self._sin_construir = 0
        self._sin_bloqueo = 0

    async def _es_segura(self, mint: str) -> bool:
        """Exige liquidez bloqueada a una moneda de origen desconocido.

        Ante la duda se descarta. Si DEXTools no contesta o no sabe el
        porcentaje, no hay motivo para asumir lo mejor: aqui "no lo se"
        cuesta lo mismo que "esta mal".
        """
        seguridad = await self._dextools.get_token_safety(mint)
        if seguridad is None or seguridad.lp_locked_pct is None:
            logger.info("Descarto %s: DEXTools no informa del bloqueo", mint)
            return False
        if seguridad.is_honeypot:
            logger.info("Descarto %s: la auditoria lo marca como honeypot", mint)
            return False
        if seguridad.freeze_authority_active:
            logger.info("Descarto %s: conserva autoridad de congelacion", mint)
            return False
        if seguridad.lp_locked_pct < self.min_lp_locked_pct:
            logger.info(
                "Descarto %s: liquidez bloqueada %.0f%% < %.0f%% exigido",
                mint,
                seguridad.lp_locked_pct,
                self.min_lp_locked_pct,
            )
            return False
        return True

    async def _construir(self, pool: DextoolsHotPool) -> TokenInfo | None:
        """Convierte una entrada del ranking en algo comprable.

        El sufijo del mint distingue la procedencia, igual que en la fuente
        de DexScreener: una moneda de pump.fun trae curva y creador, una de
        fuera solo lleva lo que Jupiter necesita y tiene que pasar por el
        control de liquidez bloqueada.
        """
        if pool.mint.endswith("pump"):
            return await build_pumpfun_token_info(
                pool.mint,
                self._impls,
                self._client,
                symbol=pool.symbol or "",
                name=pool.name or "",
                permitir_migradas=self.incluir_migradas,
            )
        if not self.incluir_cualquier_origen:
            return None
        if not await self._es_segura(pool.mint):
            self._sin_bloqueo += 1
            return None
        return await build_generic_token_info(
            pool.mint,
            self._client,
            symbol=pool.symbol or "",
            name=pool.name or "",
        )

    async def _emitir(
        self,
        pool: DextoolsHotPool,
        token_callback: Callable[[TokenInfo], Awaitable[None]],
    ) -> None:
        """Construye y entrega una entrada del ranking, si es comprable."""
        token_info = await self._construir(pool)
        if token_info is None:
            self._sin_construir += 1
            return
        importe = importe_por_rango(
            pool.rank,
            self.normal_buy_amount,
            self.promoted_rank,
            self.promoted_buy_amount,
        )
        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "acquisition_source": "dextools_hotpools",
            "dextools_rank": pool.rank,
            "signal_buy_amount": importe,
        }
        self._comprables += 1
        logger.info(
            "DEXTboard entrante -> %s (%s), puesto %s, importe %.3f SOL",
            token_info.symbol,
            pool.mint,
            pool.rank,
            importe,
        )
        await token_callback(token_info)

    def _send_heartbeat_if_due(self) -> None:
        """Manda un aviso de estado al intervalo configurado."""
        now = monotonic()
        if self.heartbeat_interval <= 0:
            return
        if (
            self._last_heartbeat
            and now - self._last_heartbeat < self.heartbeat_interval
        ):
            return
        from monitoring.notifier import notify

        notify(
            build_dextools_heartbeat(
                self.poll_interval,
                self._entrantes,
                self._comprables,
                self._dextools.tier,
                configured=self._dextools.configured,
                paper_mode=self.paper_mode,
                embudo={
                    "sin_construir": self._sin_construir,
                    "sin_bloqueo": self._sin_bloqueo,
                },
            )
        )
        self._last_heartbeat = now
        self._entrantes = 0
        self._comprables = 0
        self._sin_construir = 0
        self._sin_bloqueo = 0

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Sondea el DEXTboard y emite las entradas nuevas hasta que se pare."""
        if not self._dextools.configured:
            logger.error(
                "Fuente DEXTools: falta DEXTOOLS_API_KEY en el .env; no arranca"
            )
            return
        logger.info(
            "Fuente DEXTools activa: plan %s, cada %ss, top %s",
            self._dextools.tier,
            self.poll_interval,
            self.limit,
        )
        try:
            while True:
                try:
                    pools = await self._dextools.get_hotpools(limit=self.limit)
                    if not pools:
                        self._send_heartbeat_if_due()
                        await asyncio.sleep(self.poll_interval)
                        continue
                    if not self._primed:
                        self._tracker.prime(pools)
                        self._primed = True
                        logger.info(
                            "DEXTboard: foto inicial de %d entradas (sin comprar)",
                            len(pools),
                        )
                    else:
                        for pool in pools:
                            if not self._tracker.accept(pool):
                                continue
                            self._entrantes += 1
                            await self._emitir(pool, token_callback)
                    self._send_heartbeat_if_due()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Error en la fuente de DEXTools")
                await asyncio.sleep(self.poll_interval)
        finally:
            await self._dextools.close()
