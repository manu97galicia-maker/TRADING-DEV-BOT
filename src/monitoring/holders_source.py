"""Fuente de compra: N titulares y menos de 5 minutos de vida. Nada mas.

LO PEDIDO, literal: "que me compre cuando haya 99 holders en una moneda y que
tenga menos de cinco minutos de creacion, solo esas dos condiciones, no quiero
mas condiciones". El 19-sep el corte bajo a 90, tambien pedido.

EL NUMERO NO SE ESCRIBE EN NINGUN ROTULO, a proposito: sale del ajuste
`min_holders` y se imprime desde ahi. Cuando estaba escrito a mano en los
mensajes, bajarlo a 90 habria dejado el parte de Telegram y el guion de
arranque diciendo 99 para siempre.

QUE SE PIERDE AL QUITAR LO DEMAS, y hay que tenerlo escrito. Esta fuente NO
mira si la moneda es un bundle. Un bundle de pump and dump se monta con muchas
carteras comprando a la vez que nace la moneda: cumple las DOS condiciones de
esta estrategia a la perfeccion --muchos titulares, recien creada-- y es
exactamente lo que se lleva meses intentando evitar. Se hace asi porque es lo
pedido; el freno existe y esta a una linea de volver (`max_carteras_primer_bloque`
en la fuente de presion, que sigue en el repo apagada).

DE DONDE SALEN LOS DOS DATOS, y por que del stream y no de un indexador:

* TITULARES: `volumen.titulares()` cuenta las wallets distintas que han
  comprado y NO han soltado, sumando y restando cada operacion del stream.
  Cuesta cero y esta en el mismo instante. Un indexador tardaria segundos y a
  esta edad ni conoce la mitad de las monedas.

* EDAD: se mide desde el primer TradeEvent visto, que en pump.fun es la compra
  de creacion. Eso solo vale si el bot estaba escuchando cuando nacio: de una
  moneda que ya existia, lo unico que se sabe es cuanto hace que se la mira,
  que puede ser diez segundos en una moneda de dos horas. Por eso se exige
  haber visto la creacion -- NO es una condicion de mas, es la unica forma de
  saber la edad. Y con un tope de cinco minutos no cuesta nada: cualquier
  moneda que cumpla nacio mientras el bot ya estaba escuchando.
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

#: Evaluaciones simultaneas. Cada una hace varias llamadas al nodo; sin tope,
#: una racha se comeria el presupuesto que necesitan los stop loss.
_MAX_EN_VUELO = 4


def build_holders_heartbeat(
    min_holders: int, max_edad_seg: float, buy_amount: float, embudo: dict[str, int],
    *, paper_mode: bool,
) -> str:
    """Parte periodico para Telegram: el embudo entero, no solo el final.

    "0 compradas" puede ser que no llegue nada o que llegue y se caiga todo por
    el camino, y son problemas opuestos con arreglos opuestos.
    """
    return "\n".join([
        "👥 <b>TITULARES</b>",
        f"Disparo: <b>{min_holders}</b> titulares · menos de "
        f"{max_edad_seg / 60:.0f} min · {buy_amount:g} SOL",
        f"👀 Monedas con movimiento: <b>{embudo.get('vistas', 0)}</b>",
        f"  ↳ llegaron a {min_holders} titulares: {embudo.get('con_titulares', 0)}",
        f"  ↳ ya los tenían al verlas (no se vio el cruce): "
        f"{embudo.get('ya_venian_cumplidas', 0)}",
        f"  ↳ descartadas por viejas: {embudo.get('viejas', 0)}",
        f"  ↳ con una venta reciente: {embudo.get('venta_reciente', 0)}",
        f"  ↳ cayendo (salía más de lo que entraba): {embudo.get('dip', 0)}",
        f"  ↳ sin datos de presión: {embudo.get('sin_datos_presion', 0)}",
        f"  ↳ sin ver su creación (edad desconocida): {embudo.get('sin_historia', 0)}",
        f"  ↳ <b>comprables: {embudo.get('comprables', 0)}</b>",
        f"Modo: {'PAPER' if paper_mode else 'REAL'}",
    ])


class HoldersSource:
    """Compra cuando una moneda joven junta bastantes titulares."""

    def __init__(  # noqa: PLR0913
        self,
        wss_endpoint: str,
        platform_impls: object,
        client: object,
        *,
        min_holders: int = 90,
        max_edad_seg: float = 300.0,
        #: Operaciones seguidas que tienen que ser COMPRAS justo antes de
        #: entrar. 0 = no se mira.
        ultimas_compras: int = 2,
        #: Ventana en la que se compara dinero que entra contra dinero que
        #: sale. 0 = no se mira.
        presion_seg: float = 3.0,
        buy_amount: float = 0.07,
        heartbeat_interval: float = 300,
        paper_mode: bool = False,
    ) -> None:
        self.wss_endpoint = wss_endpoint
        self._impls = platform_impls
        self._client = client
        self.min_holders = int(min_holders)
        self.max_edad_seg = float(max_edad_seg)
        self.ultimas_compras = int(ultimas_compras or 0)
        self.presion_seg = float(presion_seg or 0)
        self.buy_amount = float(buy_amount)
        self.heartbeat_interval = float(heartbeat_interval)
        self.paper_mode = bool(paper_mode)

        self._decididas: set[str] = set()
        #: Monedas que se han visto POR DEBAJO del corte. Una moneda solo puede
        #: dispararse si esta aqui: ver "Condicion 1" en `_revisar`.
        self._vistas_por_debajo: set[str] = set()
        self._en_vuelo: set[asyncio.Task] = set()
        self._last_heartbeat: float = 0.0
        self._embudo: dict[str, int] = {}
        #: Que monedas ya se contaron en cada escalon del embudo.
        self._contadas: dict[str, set[str]] = {}

    # ------------------------------------------------------------------ datos

    def _cuenta(self, clave: str, mint: str = "") -> None:
        """Suma uno al embudo. Con `mint`, solo la PRIMERA vez de esa moneda.

        POR QUE. `_revisar` corre en CADA operacion de la moneda, asi que una
        sola moneda activa pasaba por el mismo escalon decenas de veces y el
        parte de Telegram decia "llegaron a 90 titulares: 47" con 3 monedas
        detras. El parte es lo unico que se lee desde el movil para decidir si
        el corte esta bien puesto: si infla, la decision se toma con un numero
        inventado.
        """
        if mint:
            visto = self._contadas.setdefault(clave, set())
            if mint in visto:
                return
            if len(visto) >= _MAX_RECORDADAS:
                visto.clear()
            visto.add(mint)
        self._embudo[clave] = self._embudo.get(clave, 0) + 1

    def _ya_decidida(self, mint: str) -> bool:
        if mint in self._decididas:
            return True
        if len(self._decididas) >= _MAX_RECORDADAS:
            self._decididas.clear()
        self._decididas.add(mint)
        return False

    def _apunta_por_debajo(self, mint: str) -> None:
        """Deja constancia de que esta moneda se vio con pocos titulares."""
        if len(self._vistas_por_debajo) >= _MAX_RECORDADAS:
            self._vistas_por_debajo.clear()
        self._vistas_por_debajo.add(mint)

    def _edad(self, mint: str) -> float | None:
        """Segundos de vida, o None si no se puede saber.

        None cuando no se vio nacer la moneda: entonces lo unico medible es
        cuanto hace que se la mira, que en una moneda de dos horas pueden ser
        diez segundos. Devolver ese numero seria comprar creyendo que es nueva.
        """
        if not volumen.vio_la_creacion(mint):
            return None
        return float(volumen.metricas_segunda_ola(mint).get("edad", 0.0) or 0.0)

    # ---------------------------------------------------------------- decidir

    async def _evaluar(
        self, mint: str, titulares: int, edad: float,
        token_callback: Callable[[TokenInfo], Awaitable[None]],
    ) -> None:
        token_info = await build_pumpfun_token_info(
            mint, self._impls, self._client, permitir_migradas=False
        )
        if token_info is None:
            self._cuenta("sin_construir", mint)
            return

        symbol = token_info.symbol or mint[:6]
        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "acquisition_source": "holders",
            "origen": "holders",
            "signal_buy_amount": self.buy_amount,
            "titulares": titulares,
            "edad_seg": round(edad, 1),
            #: El instante en que se decidio. Lo lee el trader al confirmarse
            #: la compra para imprimir cuanto se tardo en entrar. Sin esto, ese
            #: dato --que es EL dato cuando una compra llega tarde-- habia que
            #: sacarlo restando horas a mano de dos lineas del log.
            "decidida_en": monotonic(),
        }
        self._cuenta("comprables", mint)
        logger.info(
            "TITULARES — %s (%s): %d titulares con %.0fs de vida, se compra "
            "con %.4f SOL",
            symbol, mint, titulares, edad, self.buy_amount,
        )
        await token_callback(token_info)

    def _lanzar(
        self, mint: str, titulares: int, edad: float,
        token_callback: Callable[[TokenInfo], Awaitable[None]],
    ) -> None:
        if len(self._en_vuelo) >= _MAX_EN_VUELO:
            logger.warning(
                "Titulares: %s se descarta, ya hay %d evaluaciones en marcha",
                mint, len(self._en_vuelo),
            )
            return

        async def _corre() -> None:
            try:
                await self._evaluar(mint, titulares, edad, token_callback)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Fallo evaluando %s", mint)

        tarea = asyncio.create_task(_corre())
        self._en_vuelo.add(tarea)
        tarea.add_done_callback(self._en_vuelo.discard)

    def _revisar(
        self, mints: list[str], token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        """LAS DOS CONDICIONES, y nada mas."""
        for mint in mints:
            self._cuenta("vistas", mint)

            # 1) Titulares. Va primero porque es gratis y descarta casi todo.
            #
            # SE COMPRA CUANDO LLEGA AL CORTE, NO CUANDO LO TIENE. Es lo pedido
            # ("solo compra cuando llegue a 90") y no es lo mismo: los titulares
            # solo suben, asi que una moneda que subio, se desplomo y conserva
            # 299 titulares cumple "los tiene" para siempre. Comprar por el
            # estado es comprar el cadaver; comprar por el cruce es comprar el
            # momento en que la gente entra.
            #
            # No cuesta candidatas: esto se ejecuta en CADA operacion de la
            # moneda, y una nacida con el bot escuchando empieza en 1 titular,
            # asi que su cruce siempre se ve. Las unicas que quedan fuera son
            # las que ya existian antes del arranque -- que ya se descartan de
            # todas formas en el paso 2, por no haber visto su creacion.
            titulares = volumen.titulares(mint)
            if titulares < self.min_holders:
                self._apunta_por_debajo(mint)
                continue
            if mint not in self._vistas_por_debajo:
                self._cuenta("ya_venian_cumplidas", mint)
                continue
            self._cuenta("con_titulares", mint)

            # 2) Edad.
            edad = self._edad(mint)
            if edad is None:
                self._cuenta("sin_historia", mint)
                continue
            if edad > self.max_edad_seg:
                self._cuenta("viejas", mint)
                continue

            # 3) LAS ULTIMAS OPERACIONES, TODAS COMPRAS.
            #
            # No es lo mismo que "entra mas dinero del que sale", que es lo de
            # abajo: el saldo puede salir bien mientras alguien va soltando por
            # detras. Esto exige que NADIE haya vendido en las ultimas
            # operaciones. Al medirlo fue lo que mas separo: las monedas sin
            # ventas recientes hacian x2 el 41% de las veces, contra el 17% de
            # base.
            #
            # Si todavia no hay tantas operaciones devuelve False a proposito:
            # no se puede afirmar algo de lo que no hay datos.
            if self.ultimas_compras > 0 and not volumen.ultimas_son_compra(
                mint, self.ultimas_compras
            ):
                self._cuenta("venta_reciente", mint)
                continue

            # 4) ENTRA MAS DINERO DEL QUE SALE, AHORA MISMO.
            #
            # Para no comprar el primer escalon de una caida. El precio es la
            # consecuencia; esto mira la causa --quien esta poniendo dinero-- y
            # llega antes.
            #
            # OJO CON EL CASO SIN DATOS, que ya costo una vez: si la ventana
            # esta vacia salen 0,00 y 0,00 y la condicion no se cumple. El
            # 10-ago un filtro parecido veto las seis unicas candidatas del dia
            # por ventana vacia, no por ir mal. Aqui no deberia pasar --el paso
            # 3 ya exige operaciones recientes-- pero se cuenta aparte para que
            # se VEA en el parte de Telegram si empieza a tumbarlo todo.
            if self.presion_seg > 0:
                comprado, vendido = volumen.presion(mint, self.presion_seg)
                if comprado <= 0 and vendido <= 0:
                    self._cuenta("sin_datos_presion", mint)
                    continue
                if comprado <= vendido:
                    self._cuenta("dip", mint)
                    continue

            if self._ya_decidida(mint):
                continue
            self._lanzar(mint, titulares, edad, token_callback)

    def _heartbeat_si_toca(self) -> None:
        if self.heartbeat_interval <= 0:
            return
        ahora = monotonic()
        if ahora - self._last_heartbeat < self.heartbeat_interval:
            return
        self._last_heartbeat = ahora
        try:
            from monitoring.notifier import notify

            notify(build_holders_heartbeat(
                self.min_holders, self.max_edad_seg, self.buy_amount,
                self._embudo, paper_mode=self.paper_mode,
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
        """Escucha la cadena y compra las que cumplen las dos condiciones."""
        program_id = str(self._impls.event_parser.get_program_id())
        logger.info(
            "TITULARES ACTIVA: dispara con %d titulares y menos de %.0f min "
            "de vida, %.4f SOL, solo pump.fun (programa %s). SIN anti-bundle.",
            self.min_holders, self.max_edad_seg / 60, self.buy_amount, program_id,
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
                logger.exception("Titulares: se cayo el websocket, reconectando")
                await asyncio.sleep(5)
