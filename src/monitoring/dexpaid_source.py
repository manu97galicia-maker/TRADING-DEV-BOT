"""Acquisition source: buy tokens that recently paid DexScreener.

Polls the dex-paid feed, rebuilds a TokenInfo for each fresh, non-migrated
pump.fun candidate, and hands it to the trader's normal buy path (where the
DexScreener filter still applies). This is the "comprar al pagar dex" trigger.

Measured reality: a paid order is visible minutes after payment, so this polls
on an interval rather than reacting in milliseconds.

WARNING: the buy path for existing mints is UNVERIFIED against a live buy in
this workspace — enable with a tiny buy_amount and watch the logs first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from statistics import median
from time import monotonic

from interfaces.core import TokenInfo
from trading.token_builder import (
    build_generic_token_info,
    build_pumpfun_token_info,
)
from utils.logger import get_logger

logger = get_logger(__name__)

#: Vueltas que se promedian para el ritmo del heartbeat.
_VUELTAS_MEDIDAS = 60


def build_monitor_heartbeat(  # noqa: PLR0913
    poll_interval: float,
    fresh_signals: int,
    max_payment_age_minutes: float,
    max_token_age_minutes: float,
    *,
    paper_mode: bool,
    embudo: dict[str, int] | None = None,
    ritmo: tuple[float, float] | None = None,
) -> str:
    """Build the periodic Telegram health message for the API monitor.

    Muestra el EMBUDO, no solo lo que sale por abajo: "0 comprables" puede
    significar que no llega nada, o que llega mucho y se cae todo por el
    camino, y son problemas opuestos con arreglos opuestos.
    """
    mode = "PAPER" if paper_mode else "REAL"
    lineas = [
        "🛰 <b>MONITOR ACTIVO</b>",
        f"DexScreener: última revisión correcta · cada {poll_interval:g}s",
    ]
    if ritmo and ritmo[1] > 0:
        # MEDIDO, no estimado: lo que tarda de verdad una vuelta en esta
        # maquina y con esta red.
        lineas.append(f"⏱ Vuelta real: <b>{ritmo[0]:.1f}s</b> (peor {ritmo[1]:.1f}s)")
    if embudo:
        lineas.extend(
            [
                f"👀 Candidatas vistas: <b>{embudo.get('vistas', 0)}</b>",
                f"  ↳ ya vistas antes: {embudo.get('repetidas', 0)}",
                f"  ↳ sin señal nueva: {embudo.get('no_frescas', 0)}",
                f"  ↳ migradas descartadas: {embudo.get('migradas', 0)}",
                f"  ↳ no se pudo construir: {embudo.get('sin_construir', 0)}",
            ]
        )
    lineas.extend(
        [
            f"✅ Comprables: <b>{fresh_signals}</b>",
            f"Filtros: pago ≤{max_payment_age_minutes:g}m · "
            f"moneda ≤{max_token_age_minutes:g}m",
            f"Modo: <b>{mode}</b>",
        ]
    )
    return "\n".join(lineas)


def _dexpaid_signal_amount(
    boosts: float,
    normal_amount: float,
    small_boost_amount: float,
    expensive_threshold: float,
    expensive_amount: float,
) -> float:
    """Choose the configured buy size for a paid listing or boost tier."""
    if boosts >= expensive_threshold:
        return expensive_amount
    if boosts > 0:
        return small_boost_amount
    return normal_amount


class DexPaidFreshnessTracker:
    """Accept only events appearing after startup or a real boost increase."""

    def __init__(self) -> None:
        self._boost_totals: dict[str, float] = {}
        self._paid_mints: set[str] = set()

    def prime(self, candidates: list[dict]) -> None:
        """Record the startup snapshot without treating it as a signal."""
        for candidate in candidates:
            self._remember(candidate)

    def accept(self, candidate: dict) -> bool:
        """Return true once for a new paid mint or increased/new boost."""
        mint = str(candidate.get("mint") or "")
        if not mint:
            return False
        if candidate.get("signal_type") == "boost":
            total = float(candidate.get("boosts") or 0)
            previous = self._boost_totals.get(mint)
            self._boost_totals[mint] = max(total, previous or 0)
            return previous is None or total > previous
        is_new = mint not in self._paid_mints
        self._paid_mints.add(mint)
        return is_new

    def _remember(self, candidate: dict) -> None:
        mint = str(candidate.get("mint") or "")
        if not mint:
            return
        if candidate.get("signal_type") == "boost":
            self._boost_totals[mint] = float(candidate.get("boosts") or 0)
        else:
            self._paid_mints.add(mint)


class DexPaidSource:
    """Polls dex-paid tokens and emits TokenInfos for buying."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        platform_impls: object,
        client: object,
        poll_interval: float = 30.0,
        max_age_minutes: float = 180.0,
        max_token_age_minutes: float = 30.0,
        normal_buy_amount: float = 0.15,
        small_boost_buy_amount: float = 0.17,
        boost_threshold: float = 500.0,
        boost_buy_amount: float = 0.20,
        *,
        heartbeat_interval: float = 300.0,
        paper_mode: bool = True,
        incluir_migradas: bool = False,
        incluir_cualquier_origen: bool = False,
        min_lp_locked_pct: float = 50.0,
        otros_max_holder_pct: float = 0.05,
        candidatos_por_vuelta: int = 30,
    ) -> None:
        self._impls = platform_impls
        self._client = client
        self.poll_interval = poll_interval
        self.max_age_minutes = max_age_minutes
        self.max_token_age_minutes = max_token_age_minutes
        self.normal_buy_amount = normal_buy_amount
        self.small_boost_buy_amount = small_boost_buy_amount
        self.boost_threshold = boost_threshold
        self.boost_buy_amount = boost_buy_amount
        self.heartbeat_interval = heartbeat_interval
        self.paper_mode = paper_mode
        self.incluir_migradas = incluir_migradas
        self.incluir_cualquier_origen = incluir_cualquier_origen
        self.min_lp_locked_pct = min_lp_locked_pct
        #: Tope de concentracion SOLO para las de fuera de pump.fun. Va aqui
        #: y no en el filtro global porque las dos procedencias no se pueden
        #: medir igual: en pump.fun el supply es fijo y un titular gordo es
        #: normal (el dev, quien puso la liquidez); fuera no hay nada de eso,
        #: asi que se exige mas.
        self.otros_max_holder_pct = otros_max_holder_pct
        # Cuantas entradas del feed se miran por vuelta. Estaba fijo en 10: en
        # una ventana tan estrecha una moneda entra y sale entre dos sondeos y
        # no llega a verse nunca.
        self.candidatos_por_vuelta = candidatos_por_vuelta
        self._seen: set[str] = set()
        self._freshness = DexPaidFreshnessTracker()
        self._primed = False
        self._last_heartbeat = 0.0
        self._fresh_signals = 0
        # El embudo entero, no solo lo que sale por abajo. Contando solo las
        # comprables era imposible distinguir "no llega nada" de "llega mucho
        # y se cae todo por el camino", que son problemas opuestos.
        self._vistas = 0
        self._repetidas = 0
        self._no_frescas = 0
        self._migradas = 0
        self._sin_construir = 0
        self._vueltas_seg: list[float] = []

    def _anota_vuelta(self, segundos: float) -> None:
        """Guarda cuanto ha tardado esta vuelta de deteccion.

        Se mide en vez de estimarse porque el calculo sobre el papel solo
        cubre lo que impone el limitador; lo que tarde la red del droplet en
        hablar con DexScreener no se puede saber desde otra maquina.
        """
        self._vueltas_seg.append(segundos)
        # Solo interesa lo reciente: con la ventana entera, una vuelta lenta
        # del arranque tapa para siempre el ritmo actual.
        if len(self._vueltas_seg) > _VUELTAS_MEDIDAS:
            del self._vueltas_seg[0]

    def _ritmo(self) -> tuple[float, float]:
        """Devuelve (mediana, peor) de las ultimas vueltas medidas."""
        if not self._vueltas_seg:
            return (0.0, 0.0)
        return (median(self._vueltas_seg), max(self._vueltas_seg))

    def _interesa(self, resumen: dict) -> bool:
        """Criba BARATA, antes de gastar una sola peticion por candidata.

        Antes estas dos comprobaciones vivian en `_evaluar`, o sea DESPUES
        de que el feed hubiera pedido ordenes y datos de mercado de las 90
        candidatas. Como ~85 se caen aqui, se pagaba el precio completo por
        monedas que ya se sabia que no se iban a comprar: una vuelta tardaba
        25-80 segundos en vez de los 5 configurados, y una moneda recien
        pagada podia esperar mas de un minuto a que le llegara el turno.

        Todo lo que se mira aqui viene ya en la respuesta de los feeds, asi
        que decidirlo no cuesta ni una llamada.
        """
        mint = resumen.get("mint")
        if not mint or mint in self._seen:
            self._repetidas += 1
            return False
        if not self._freshness.accept(resumen):
            self._no_frescas += 1
            return False
        return True

    async def _evaluar(self, c: dict) -> TokenInfo | None:
        """Pasa un candidato por el embudo y lo devuelve si es comprable.

        Cada descarte suma en su contador: sin eso, "0 comprables" no
        distingue entre que no llegue nada y que llegue todo y se caiga por
        el camino.
        """
        # Las dos primeras cribas (ya vista, sin senal nueva) las hizo ya
        # `_interesa` sobre la respuesta cruda del feed. Repetirlas aqui
        # daria False siempre: `_freshness.accept` guarda estado.
        mint = c.get("mint")
        if not mint:
            return None
        # Quien paga DexScreener suele hacerlo cuando la moneda ya tiene
        # traccion, y para entonces muchas han salido de la curva.
        # Descartarlas siempre tira buena parte de la senal; con
        # incluir_migradas se compran por el pool.
        if c.get("migrated") and not self.incluir_migradas:
            self._migradas += 1
            return None
        token_info = await self._construir(c)
        if token_info is None:
            self._sin_construir += 1
            return None
        boosts = float(c.get("boosts") or 0)
        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "acquisition_source": "dexscreener",
            "dexscreener_boosts": boosts,
            "signal_buy_amount": _dexpaid_signal_amount(
                boosts,
                self.normal_buy_amount,
                self.small_boost_buy_amount,
                self.boost_threshold,
                self.boost_buy_amount,
            ),
        }
        self._seen.add(mint)
        self._fresh_signals += 1
        logger.info(
            "Fresh DexPaid %s -> %s (%s), token age %s min",
            c.get("signal_type"),
            token_info.symbol,
            mint,
            c.get("token_age_min"),
        )
        return token_info

    async def _construir(self, candidato: dict) -> TokenInfo | None:
        """Decide si un candidato es comprable y lo convierte en TokenInfo.

        Separa las dos procedencias: una moneda de pump.fun (aunque haya
        migrado) conserva su curva y su creador, y se construye con todo eso;
        una de fuera no tiene nada de eso y solo lleva lo que Jupiter necesita.
        El sufijo del mint es la convencion que ya usa el lector de Telegram
        para reconocer el lanzador.
        """
        mint = str(candidato.get("mint") or "")
        if mint.endswith("pump"):
            return await build_pumpfun_token_info(
                mint,
                self._impls,
                self._client,
                symbol=candidato.get("symbol", ""),
                name=candidato.get("name", ""),
                permitir_migradas=self.incluir_migradas,
            )
        if not self.incluir_cualquier_origen or not self._pasa_rugcheck(candidato):
            return None
        token_info = await build_generic_token_info(
            mint,
            self._client,
            symbol=candidato.get("symbol", ""),
            name=candidato.get("name", ""),
        )
        if token_info is not None:
            # El comprador lee estas dos para aplicarle el regimen estricto.
            token_info.additional_data = {
                **(token_info.additional_data or {}),
                "origen": "otro",
                "max_holder_pct": self.otros_max_holder_pct,
            }
        return token_info

    def _pasa_rugcheck(self, candidato: dict) -> bool:
        """Exige liquidez bloqueada a una moneda de origen desconocido.

        Una moneda de pump.fun trae su propio contexto: el creador, su
        historial y una curva que se puede leer. Fuera de ahi no hay nada de
        eso, y ademas aparecen cosas que en pump.fun no pueden pasar, como que
        el token se pueda congelar. La liquidez bloqueada no lo cubre todo,
        pero es la senal mas directa de que quien lanzo no puede llevarse el
        pool.

        Ante la duda se descarta: si RugCheck no devolvio informe, no hay
        motivo para asumir lo mejor.
        """
        mint = candidato.get("mint")
        if candidato.get("rugged"):
            logger.info("Descarto %s: RugCheck lo marca como rugged", mint)
            return False
        bloqueada = candidato.get("lp_locked_pct")
        if bloqueada is None:
            logger.info("Descarto %s: sin informe de RugCheck", mint)
            return False
        if float(bloqueada) < self.min_lp_locked_pct:
            logger.info(
                "Descarto %s: liquidez bloqueada %.0f%% < %.0f%% exigido",
                mint,
                float(bloqueada),
                self.min_lp_locked_pct,
            )
            return False
        return True

    def _send_heartbeat_if_due(self) -> None:
        """Send one best-effort status message at the configured interval."""
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
            build_monitor_heartbeat(
                self.poll_interval,
                self._fresh_signals,
                self.max_age_minutes,
                self.max_token_age_minutes,
                paper_mode=self.paper_mode,
                embudo={
                    "vistas": self._vistas,
                    "repetidas": self._repetidas,
                    "no_frescas": self._no_frescas,
                    "migradas": self._migradas,
                    "sin_construir": self._sin_construir,
                },
                ritmo=self._ritmo(),
            )
        )
        self._last_heartbeat = now
        self._fresh_signals = 0
        self._vistas = 0
        self._repetidas = 0
        self._no_frescas = 0
        self._migradas = 0
        self._sin_construir = 0

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Continuously poll and emit new, non-migrated dex-paid candidates."""
        from dashboard.dexpaid_feed import get_candidates
        from dexscreener.client import DexScreenerClient

        logger.info(
            "DexPaid source active: poll every %ss, max age %s min",
            self.poll_interval,
            self.max_age_minutes,
        )
        # UN solo cliente para toda la vida del bucle. Creando uno por vuelta,
        # el limitador nacia con el cubo lleno cada 5 segundos y no limitaba
        # nada entre vueltas: el resultado era 429 en cadena y cero candidatas.
        dex = DexScreenerClient()
        try:
            while True:
                try:
                    _t0 = monotonic()
                    candidates = await get_candidates(
                        limit=self.candidatos_por_vuelta,
                        max_age_minutes=self.max_age_minutes,
                        max_token_age_minutes=self.max_token_age_minutes,
                        # Fuera de pump.fun no hay historial de dev ni curva que
                        # analizar, asi que RugCheck pasa de ser un extra lento a
                        # ser la unica red: se pide solo cuando hace falta.
                        check_locked=self.incluir_cualquier_origen,
                        dex=dex,
                        interesa=self._interesa,
                    )
                    self._anota_vuelta(monotonic() - _t0)
                    if not self._primed:
                        self._freshness.prime(candidates)
                        self._primed = True
                        logger.info(
                            "DexPaid startup baseline recorded (%d entries, no buys)",
                            len(candidates),
                        )
                        self._send_heartbeat_if_due()
                        await asyncio.sleep(self.poll_interval)
                        continue
                    self._vistas += len(candidates)
                    for c in candidates:
                        token_info = await self._evaluar(c)
                        if token_info is not None:
                            await token_callback(token_info)
                    self._send_heartbeat_if_due()
                except asyncio.CancelledError:
                    logger.info("DexPaid source cancelled")
                    break
                except Exception:
                    logger.exception("DexPaid source error")
                await asyncio.sleep(self.poll_interval)
        finally:
            await dex.close()
