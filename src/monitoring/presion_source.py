"""Fuente de compra: entrar cuando MUCHA GENTE DISTINTA esta comprando a la vez.

DE DONDE SALE ESTO, Y POR QUE NO ES UNA CORAZONADA. Se midieron 27.241 monedas
de pump.fun apuntando, a los 20 segundos de vida, sus senales, y siguiendolas
despues cinco minutos para ver que hacian. De todo lo que se probo, lo que mas
predijo un pump no fue la presion como ratio (compras contra ventas) sino el
NUMERO DE COMPRADORES DISTINTOS en una ventana corta:

    de base            llegan al +25%  el 14,4%
    con >=3 compradores                el 27,4%   (+13,0 puntos)
    con >=12                           el 25,8%   (+11,4 puntos)

Casi el doble que comprar a ciegas. La presion como ratio no saco ventaja
suficiente para aparecer en la lista, y eso es contraintuitivo pero es el dato:
lo que mueve una moneda no es que se compre mas de lo que se vende, es que este
entrando GENTE. Una wallet sola puede fabricar el ratio; no puede fabricar
carteras distintas sin que se note en el bloque de creacion.

POR QUE EL CORTE POR DEFECTO NO ES 3, QUE ES EL QUE MAS ACIERTA. Porque un
filtro se juzga por dos cosas y el acierto es solo una. La otra es el RITMO:
con >=3 pasan unas 3 monedas por minuto, y con cinco plazas el bot se llenaria
en dos minutos con las tres PRIMERAS que aparezcan -- que no son las mejores,
son las primeras. A partir de ahi quien elige es el reloj, no el filtro. Con
>=12 pasan unas 0,7 por minuto, que es el ritmo al que las cinco plazas de
verdad eligen. Se pierde 1,6 puntos de acierto y se gana poder elegir.

LO QUE NO COMPRA. Los mismos bundles que descarta la fuente de la curva, y por
la misma razon: un bundle se monta en el bloque de creacion --varias carteras
del mismo dueno comprando a la vez que nace la moneda-- y desde fuera parece
justo lo que esta fuente busca, mucha gente comprando de golpe. Sin ese freno,
este filtro seria un iman de pump and dump.

Como las demas fuentes, abre su propio `logsSubscribe`: cuando hay fuentes de
adquisicion activas el trader no arranca el listener de creaciones, asi que
nadie estaria alimentando `volumen`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from time import monotonic

import websockets

from interfaces.core import TokenInfo
from monitoring import volumen
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)

#: Monedas recordadas como "ya decidida". Se decide UNA vez por moneda: sin
#: esto, una que cruza el corte lo seguiria cruzando en cada operacion.
_MAX_RECORDADAS = 20_000

#: Evaluaciones simultaneas. Cada una hace varias llamadas al nodo para
#: reconstruir la moneda; sin tope, una racha se comeria el presupuesto de RPC
#: que necesitan los stop loss de lo ya comprado.
_MAX_EN_VUELO = 4


def build_presion_heartbeat(
    min_compradores_ola: int,
    buy_amount: float,
    embudo: dict[str, int],
    *,
    paper_mode: bool,
) -> str:
    """Mensaje periodico de salud para Telegram.

    Muestra el embudo entero y no solo lo que sale por abajo: "0 compradas"
    puede ser que no llegue nada o que llegue y se caiga todo por el camino, y
    son problemas opuestos con arreglos opuestos.
    """
    modo = "PAPER" if paper_mode else "REAL"
    return "\n".join([
        "🌊 <b>PRESIÓN COMPRADORA</b>",
        f"Disparo: <b>{min_compradores_ola}</b> compradores distintos · "
        f"{buy_amount:g} SOL",
        f"👀 Monedas con movimiento: <b>{embudo.get('vistas', 0)}</b>",
        f"  ↳ llegaron al corte: {embudo.get('en_umbral', 0)}",
        f"  ↳ bundle descartado: {embudo.get('bundles', 0)}",
        f"  ↳ ya migradas: {embudo.get('sin_construir', 0)}",
        f"  ↳ <b>comprables: {embudo.get('comprables', 0)}</b>",
        f"Modo: {modo}",
    ])


class PresionSource:
    """Vigila quien esta comprando y dispara cuando entra gente de golpe."""

    def __init__(  # noqa: PLR0913
        self,
        wss_endpoint: str,
        platform_impls: object,
        client: object,
        *,
        min_compradores_ola: int = 12,
        ventana: float = 5.0,
        buy_amount: float = 0.07,
        max_avance_curva: float = 0.95,
        max_carteras_primer_bloque: int = 3,
        max_sol_primer_bloque: float = 2.0,
        max_bundle_pct: float = 0.5,
        max_holder_pct: float = 0.30,
        max_holder_pct_sin_historia: float = 0.25,
        exigir_ver_creacion: bool = True,
        max_bundle_pct_sin_historia: float = 0.35,
        heartbeat_interval: float = 300,
        paper_mode: bool = False,
    ) -> None:
        self.wss_endpoint = wss_endpoint
        self._impls = platform_impls
        self._client = client
        self.min_compradores_ola = int(min_compradores_ola)
        self.ventana = float(ventana)
        self.buy_amount = float(buy_amount)
        #: Por encima de este avance ya no se entra: una curva casi llena esta
        #: a punto de migrar y ese momento lo cubre la fuente `curva_96`, con
        #: sus propias reglas. Entrar aqui tambien seria comprar dos veces la
        #: misma idea con dinero distinto.
        self.max_avance_curva = float(max_avance_curva)
        self.max_carteras_primer_bloque = int(max_carteras_primer_bloque)
        self.max_sol_primer_bloque = float(max_sol_primer_bloque)
        self.max_bundle_pct = float(max_bundle_pct)
        #: Cuanto puede concentrar UN SOLO titular. Esta fuente trae el suyo
        #: porque el tope general (0,15) esta pensado para monedas recien
        #: nacidas, donde nadie ha tenido tiempo de acumular. Esta compra a
        #: media vida: alli un titular con mas del 15% es lo normal y ese tope
        #: las descarta TODAS. El mismo problema que ya tenia la fuente de la
        #: curva, y que su comentario en `_top_holder_exceeds` ya avisaba.
        #:
        #: Subirlo afloja una proteccion de verdad: con el 30% en una sola
        #: mano, esa mano puede hundir la moneda cuando quiera. Es el precio
        #: de comprar a media vida.
        self.max_holder_pct = float(max_holder_pct)
        #: Y mas apretado cuando no se vio nacer la moneda, por lo mismo que
        #: el tope de bundle: alli no hay nada mas que mire.
        self.max_holder_pct_sin_historia = float(max_holder_pct_sin_historia)
        #: Si se exige haber visto NACER la moneda. En True el bot arranca
        #: ciego: solo puede comprar lo que nazca despues, porque de lo que ya
        #: existia no tiene los contadores del bloque de creacion. Es lo mas
        #: seguro y lo mas lento.
        self.exigir_ver_creacion = bool(exigir_ver_creacion)
        #: MAS APRETADO CUANDO SE VA A CIEGAS. Sin haber visto nacer la
        #: moneda, los avisos del bloque de creacion no existen y todo el peso
        #: recae en este tope de concentracion en cadena, que se comprueba
        #: antes de comprar y no necesita historia.
        self.max_bundle_pct_sin_historia = float(max_bundle_pct_sin_historia)
        self.heartbeat_interval = float(heartbeat_interval)
        self.paper_mode = bool(paper_mode)

        self._decididas: set[str] = set()
        self._en_vuelo: set[asyncio.Task] = set()
        self._last_heartbeat: float = 0.0
        self._embudo: dict[str, int] = {}

    # ------------------------------------------------------------------ datos

    def _cuenta(self, clave: str) -> None:
        self._embudo[clave] = self._embudo.get(clave, 0) + 1

    def _ya_decidida(self, mint: str) -> bool:
        """True si esta moneda ya se miro. Solo se decide UNA vez."""
        if mint in self._decididas:
            return True
        if len(self._decididas) >= _MAX_RECORDADAS:
            self._decididas.clear()
        self._decididas.add(mint)
        return False

    # ----------------------------------------------------------------- bundle

    def _es_bundle(self, mint: str, symbol: str) -> bool:
        """True si la oleada de compradores huele a bundle montado.

        SIN ESTE FRENO, ESTA FUENTE SERIA UN IMAN DE PUMP AND DUMP. Un bundle
        es exactamente "muchas carteras comprando a la vez", que es lo que el
        filtro busca; la diferencia es CUANDO. Las del bundle entran en el
        bloque de creacion, todas juntas y antes que nadie, y eso se ve en el
        stream sin preguntar a ningun indexador.

        Si no se vio nacer la moneda, los contadores del bloque de creacion
        valen cero y pasarian solos -- que es peor que no mirarlos, porque
        daria un visto bueno que nadie ha dado. En ese caso se deja pasar la
        moneda: el freno que queda (la concentracion de titulares) lo aplica
        el filtro general antes de comprar, pero no alcanza para dar por
        limpia una oleada que no se ha visto empezar.
        """
        if not volumen.vio_la_creacion(mint):
            self._cuenta("sin_historia")
            if self.exigir_ver_creacion:
                logger.info(
                    "%s (%s): oleada de compradores pero no se vio nacer la "
                    "moneda, así que no se puede descartar un bundle. Se deja "
                    "pasar.", symbol, mint,
                )
                return True
            # MODO FLEXIBLE. Se compra igual, pero sabiendo lo que se pierde:
            # los dos avisos de abajo (cuantas carteras y cuanto SOL entraron
            # en el bloque de creacion) valen CERO sin historia y pasarian
            # solos, asi que no se miran -- mirarlos seria darse un visto
            # bueno que nadie ha dado. Todo el peso recae en el tope de
            # concentracion en cadena, que va mas apretado para estas y lo
            # comprueba el filtro general justo antes de comprar.
            logger.info(
                "%s (%s): no se vio nacer; se compra igual con el tope de "
                "concentración apretado al %.0f%%",
                symbol, mint, self.max_bundle_pct_sin_historia * 100,
            )
            return False

        carteras = volumen.carteras_primer_bloque(mint)
        if (
            self.max_carteras_primer_bloque > 0
            and carteras > self.max_carteras_primer_bloque
        ):
            logger.info(
                "BUNDLE descartado %s (%s): %d carteras compraron en el bloque "
                "de creación (máx %d)",
                symbol, mint, carteras, self.max_carteras_primer_bloque,
            )
            self._cuenta("bundles")
            return True

        sol_inicial = volumen.sol_primer_bloque(mint)
        if self.max_sol_primer_bloque > 0 and sol_inicial > self.max_sol_primer_bloque:
            logger.info(
                "BUNDLE descartado %s (%s): %.2f SOL cargados en el bloque de "
                "creación (máx %.2f)",
                symbol, mint, sol_inicial, self.max_sol_primer_bloque,
            )
            self._cuenta("bundles")
            return True
        return False

    # ---------------------------------------------------------------- decidir

    def _oleada(self, mint: str) -> int:
        """Compradores DISTINTOS en la ventana corta. Es la señal."""
        m = volumen.metricas_segunda_ola(mint, ventana=self.ventana)
        return int(m.get("ola_compradores", 0) or 0)

    async def _evaluar(
        self, mint: str, token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        """Comprueba una moneda con oleada y la manda a comprar."""
        compradores = self._oleada(mint)
        if self._es_bundle(mint, mint[:6]):
            return

        token_info = await build_pumpfun_token_info(
            mint, self._impls, self._client, permitir_migradas=False
        )
        if token_info is None:
            self._cuenta("sin_construir")
            return

        symbol = token_info.symbol or mint[:6]
        avance = volumen.progreso_curva(mint)
        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "acquisition_source": "presion",
            "origen": "presion",
            "signal_buy_amount": self.buy_amount,
            "max_bundle_pct": (
                self.max_bundle_pct
                if volumen.vio_la_creacion(mint)
                else self.max_bundle_pct_sin_historia
            ),
            "max_holder_pct": (
                self.max_holder_pct
                if volumen.vio_la_creacion(mint)
                else self.max_holder_pct_sin_historia
            ),
            "ola_compradores": compradores,
            "curva_progreso": avance[0] if avance else None,
        }
        self._cuenta("comprables")
        logger.info(
            "PRESIÓN — %s (%s): %d compradores distintos en %.0fs, se compra "
            "con %.4f SOL",
            symbol, mint, compradores, self.ventana, self.buy_amount,
        )
        await token_callback(token_info)

    def _lanzar(
        self, mint: str, token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        """Evalua aparte para no parar la lectura del websocket."""
        if len(self._en_vuelo) >= _MAX_EN_VUELO:
            logger.warning(
                "Presión: %s se descarta, ya hay %d evaluaciones en marcha",
                mint, len(self._en_vuelo),
            )
            return

        async def _corre() -> None:
            try:
                await self._evaluar(mint, token_callback)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Fallo evaluando la presión de %s", mint)

        tarea = asyncio.create_task(_corre())
        self._en_vuelo.add(tarea)
        tarea.add_done_callback(self._en_vuelo.discard)

    def _revisar(
        self, mints: list[str], token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        """Mira solo las monedas que acaban de operar."""
        for mint in mints:
            self._cuenta("vistas")
            if self._oleada(mint) < self.min_compradores_ola:
                continue
            avance = volumen.progreso_curva(mint)
            if avance and avance[0] > self.max_avance_curva:
                # Esa la coge la fuente de la curva, con sus propias reglas.
                continue
            self._cuenta("en_umbral")
            if self._ya_decidida(mint):
                continue
            self._lanzar(mint, token_callback)

    def _heartbeat_si_toca(self) -> None:
        if self.heartbeat_interval <= 0:
            return
        ahora = monotonic()
        if ahora - self._last_heartbeat < self.heartbeat_interval:
            return
        self._last_heartbeat = ahora
        try:
            from monitoring.notifier import notify

            notify(build_presion_heartbeat(
                self.min_compradores_ola, self.buy_amount, self._embudo,
                paper_mode=self.paper_mode,
            ))
        except Exception:  # noqa: BLE001, S110
            pass

    # ------------------------------------------------------------------ bucle

    async def _suscribir(self, websocket: object, program_id: str) -> None:
        await websocket.send(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
            "params": [{"mentions": [program_id]}, {"commitment": "processed"}],
        }))
        respuesta = json.loads(await websocket.recv())
        if "result" not in respuesta:
            logger.warning("Respuesta rara al suscribirse: %s", respuesta)

    async def _escuchar(
        self, websocket: object, token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        while True:
            crudo = await asyncio.wait_for(websocket.recv(), timeout=60)
            datos = json.loads(crudo)
            if datos.get("method") != "logsNotification":
                continue
            resultado = datos["params"]["result"]
            tocadas = volumen.registrar_logs(
                resultado.get("value", {}).get("logs", []),
                slot=resultado.get("context", {}).get("slot"),
            )
            self._revisar(tocadas, token_callback)
            self._heartbeat_si_toca()

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Escucha la cadena y compra las monedas con oleada de compradores."""
        program_id = str(self._impls.event_parser.get_program_id())
        logger.info(
            "PRESIÓN COMPRADORA ACTIVA: dispara con %d compradores distintos "
            "en %.0fs, %.4f SOL, solo pump.fun (programa %s)",
            self.min_compradores_ola, self.ventana, self.buy_amount, program_id,
        )
        while True:
            try:
                async with websockets.connect(
                    self.wss_endpoint, ping_interval=20
                ) as ws:
                    await self._suscribir(ws, program_id)
                    await self._escuchar(ws, token_callback)
            except asyncio.CancelledError:
                for tarea in list(self._en_vuelo):
                    tarea.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await tarea
                raise
            except Exception:
                logger.exception("Presión: se cayó el websocket, reconectando")
                await asyncio.sleep(5)
