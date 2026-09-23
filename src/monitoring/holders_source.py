"""Fuente de compra: N titulares cruzados, y presion compradora. Nada mas.

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

* EDAD: se mide desde la marca de tiempo DE LA CADENA del primer TradeEvent
  visto, que en pump.fun es la compra de creacion. Eso solo vale si el bot
  estaba escuchando cuando nacio: de una moneda que ya existia, lo unico que
  se sabe es cuanto hace que se la mira, que puede ser diez segundos en una
  moneda de dos horas. Por eso, MIENTRAS SE MIRA LA EDAD, se exige haber visto
  la creacion: no es una condicion de mas, es la unica forma de saberla.

EL 22-SEP SE APAGO EL CORTE DE EDAD (`max_edad_seg: 0`), pedido, y con el cae
tambien el requisito de haber visto nacer la moneda -- porque ese requisito
existia solo para poder medir la edad. Era ademas el filtro mas duro de todos:
descartaba TODAS las monedas vivas cada vez que se reiniciaba el bot, que en
una maquina que se reinicia a menudo son casi todas.

QUE QUEDA SUJETANDO LA ENTRADA, entonces:

* el CRUCE del corte de titulares (paso 1): no basta con que la moneda TENGA
  23 titulares, hay que verla pasar de menos a mas. Una moneda muerta no suma
  gente nueva, asi que esto ya descarta los cadaveres;
* la PRESION COMPRADORA (ultimo paso): que ahora mismo entre mas dinero del
  que sale.
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


def _sin_etiquetas(texto: str) -> str:
    """Quita el <b> de Telegram para que el parte se lea igual en el log."""
    return texto.replace("<b>", "").replace("</b>", "")

#: Monedas recordadas como "ya decidida". Se decide UNA vez por moneda: sin
#: esto, una que cruza el corte lo seguiria cruzando en cada operacion.
_MAX_RECORDADAS = 20_000

#: Evaluaciones simultaneas. Cada una hace UNA vuelta al nodo (antes tres), y
#: el presupuesto subio de 15 a 45 consultas/s con el plan de Helius, asi que
#: 4 se quedaba muy corto: con el corte en 23 titulares hay muchas monedas
#: cumpliendo a la vez y las que no cabian se perdian.
_MAX_EN_VUELO = 12

#: Espera entre reintentos de conexion, en segundos. SE DOBLA en cada fallo
#: seguido y se reinicia al conectar.
#:
#: ANTES ERA 5 FIJO Y PARA SIEMPRE, y eso fue lo que tuvo el bot dos dias sin
#: comprar: el nodo contestaba 429 ("demasiadas peticiones"), el bot reintentaba
#: 720 veces por hora, y ese martilleo mantenia vivo el 429. No se recuperaba
#: solo nunca.
_ESPERA_RECONEXION = 5.0
_ESPERA_RECONEXION_MAX = 300.0

#: Un 429 no es un corte de red: es una cuota. Reintentar enseguida solo la
#: gasta mas, asi que se empieza alto en vez de subir poco a poco.
_ESPERA_TRAS_429 = 60.0


def build_holders_heartbeat(
    min_holders: int, max_edad_seg: float, buy_amount: float, embudo: dict[str, int],
    *, paper_mode: bool,
) -> str:
    """Parte periodico: el embudo entero, no solo el final.

    "0 compradas" puede ser que no llegue nada o que llegue y se caiga todo por
    el camino, y son problemas opuestos con arreglos opuestos.

    LLEVA ADEMAS LAS DOS MEDIDAS QUE FALTABAN, porque "el numero de titulares
    tarda muchisimo" tiene tres causas posibles y sin datos no se distinguen:

    * el RETRASO dice si los datos llegan tarde, en cuyo caso el recuento es
      exacto pero de hace segundos. Contar titulares esta medido en 0,03 ms,
      asi que lo lento nunca es contar;
    * las monedas BORRADAS POR TOPE dicen si la memoria se queda corta, que es
      lo que hace que a una moneda el recuento le vuelva a empezar en cero;
    * y si esas dos salen bien, entonces las monedas sencillamente tardan en
      juntar titulares y lo que hay que mover es el corte.
    """
    p50, p95, muestras = volumen.retraso()
    mem = volumen.estado_memoria()
    # "menos de 0 min" seria mentira y ademas alarmante: con el corte de edad
    # apagado no es que el limite sea cero, es que no hay limite.
    edad_txt = (
        f"menos de {max_edad_seg / 60:.0f} min"
        if max_edad_seg > 0
        else "sin límite de edad"
    )
    return "\n".join([
        "👥 <b>TITULARES</b>",
        f"Disparo: <b>{min_holders}</b> titulares · {edad_txt} · "
        f"{buy_amount:g} SOL",
        f"👀 Monedas con movimiento: <b>{embudo.get('vistas', 0)}</b>",
        f"  ↳ llegaron a {min_holders} titulares: {embudo.get('con_titulares', 0)}",
        f"  ↳ ya los tenían al verlas (no se vio el cruce): "
        f"{embudo.get('ya_venian_cumplidas', 0)}",
        f"  ↳ descartadas por viejas: {embudo.get('viejas', 0)}",
        f"  ↳ con una venta reciente: {embudo.get('venta_reciente', 0)}",
        f"  ↳ cayendo (salía más de lo que entraba): {embudo.get('dip', 0)}",
        f"  ↳ detrás de una vela verde (ya habían subido): "
        f"{embudo.get('vela_verde', 0)}",
        f"  ↳ con poco volumen (entraba calderilla): "
        f"{embudo.get('poco_volumen', 0)}",
        f"  ↳ sin datos de presión: {embudo.get('sin_datos_presion', 0)}",
        f"  ↳ sin ver su creación (edad desconocida): {embudo.get('sin_historia', 0)}",
        f"  ↳ <b>comprables: {embudo.get('comprables', 0)}</b>",
        f"⏱️ Retraso del stream: <b>{p50:.0f}s</b> (p95 {p95:.0f}s, "
        f"{muestras} muestras)",
        f"🧠 Memoria: {mem['monedas']}/{mem['tope']} monedas · "
        f"{mem['ventana_seg'] // 60} min · borradas por tope: "
        f"<b>{mem['por_tope']}</b>",
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
        #: NO COMPRAR EL ULTIMO ESCALON DE UNA VELA VERDE. Si el precio ha
        #: subido mas de `max_subida` (tanto por uno) en `subida_seg`
        #: segundos, la moneda ya ha hecho el movimiento y se deja pasar.
        #: 0 = no se mira.
        subida_seg: float = 10.0,
        max_subida: float = 0.0,
        #: SOL comprados como minimo en la ventana de presion. Sin esto,
        #: "entra mas de lo que sale" lo cumple una moneda en la que entran
        #: dos centimos y salen uno. 0 = no se mira.
        volumen_min_sol: float = 0.0,
        buy_amount: float = 0.07,
        heartbeat_interval: float = 300,
        paper_mode: bool = False,
        #: Endpoint gRPC de Geyser. Si esta puesto, los datos entran por ahi en
        #: vez de por el websocket: llegan del validador directamente, sin
        #: pasar por la capa RPC, y eso son 100-300 ms menos en enterarse. Sin
        #: endpoint no cambia nada: se sigue usando el websocket de siempre.
        geyser_endpoint: str = "",
        geyser_token: str = "",
        geyser_auth: str = "x-token",
    ) -> None:
        self.wss_endpoint = wss_endpoint
        self._impls = platform_impls
        self._client = client
        self.min_holders = int(min_holders)
        self.max_edad_seg = float(max_edad_seg)
        self.ultimas_compras = int(ultimas_compras or 0)
        self.presion_seg = float(presion_seg or 0)
        self.subida_seg = float(subida_seg or 0)
        self.max_subida = float(max_subida or 0)
        self.volumen_min_sol = float(volumen_min_sol or 0)
        self.buy_amount = float(buy_amount)
        self.heartbeat_interval = float(heartbeat_interval)
        self.paper_mode = bool(paper_mode)
        self.geyser_endpoint = (geyser_endpoint or "").strip()
        self.geyser_token = (geyser_token or "").strip()
        self.geyser_auth = (geyser_auth or "x-token").strip().lower()

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
        return volumen.edad(mint)

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
    ) -> bool:
        """Lanza la evaluacion. Devuelve si de verdad se lanzo.

        EL VALOR DE VUELTA IMPORTA. Antes esto no devolvia nada y quien llamaba
        ya habia marcado la moneda como "decidida" -- asi que una moneda
        descartada por tope de concurrencia quedaba marcada PARA SIEMPRE y no
        se volvia a mirar nunca, aunque un segundo despues hubiera hueco de
        sobra. Con el corte en 23 titulares hay muchas cumpliendo a la vez, asi
        que eso tiraba a la basura buena parte de las candidatas.
        """
        if len(self._en_vuelo) >= _MAX_EN_VUELO:
            logger.warning(
                "Titulares: %s se aparca, ya hay %d evaluaciones en marcha "
                "(se volvera a mirar)",
                mint, len(self._en_vuelo),
            )
            return False

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
        return True

    def _edad_vale(self, mint: str, edad: float | None) -> bool:
        """Si la moneda pasa el corte de edad. Con el corte en 0, siempre.

        Apagar el corte apaga TAMBIEN el requisito de haber visto nacer la
        moneda, y va junto a proposito: ese requisito nunca fue un filtro de
        calidad, era la unica forma de SABER la edad. Sin mirar la edad no
        protege de nada, y era el filtro mas duro que habia -- descartaba todas
        las monedas vivas en cada reinicio del bot.
        """
        if self.max_edad_seg <= 0:
            return True
        if edad is None:
            self._cuenta("sin_historia", mint)
            return False
        if edad > self.max_edad_seg:
            self._cuenta("viejas", mint)
            return False
        return True

    def _el_dinero_acompana(self, mint: str) -> bool:
        """Que entre mas dinero del que sale, Y que sea dinero de verdad.

        SON DOS COSAS DISTINTAS y hacen falta las dos. La primera mira la
        PROPORCION: si las ventas aplastan a las compras, la moneda se esta
        cayendo ahora mismo y el precio es solo la consecuencia. La segunda
        mira el TAMANO: "entra mas de lo que sale" lo cumple igual de bien una
        moneda en la que entran dos centimos y sale uno, y una moneda sin
        dinero detras no se mueve por perfecta que sea la proporcion.

        OJO CON EL CASO SIN DATOS, que ya costo una vez: con la ventana vacia
        salen 0,00 y 0,00 y la condicion no se cumple. El 10-ago un filtro
        parecido veto las seis unicas candidatas del dia por ventana vacia, no
        por ir mal. Se cuenta aparte para que se VEA en el parte si empieza a
        tumbarlo todo.
        """
        if self.presion_seg <= 0:
            return True
        comprado, vendido = volumen.presion(mint, self.presion_seg)
        if comprado <= 0 and vendido <= 0:
            self._cuenta("sin_datos_presion", mint)
            return False
        if comprado <= vendido:
            self._cuenta("dip", mint)
            return False
        if self.volumen_min_sol > 0 and comprado < self.volumen_min_sol:
            self._cuenta("poco_volumen", mint)
            return False
        return True

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

            # 2) EDAD. Con `max_edad_seg` en 0 NO SE MIRA, y se deja de exigir
            # tambien haber visto nacer la moneda (22-sep, pedido).
            #
            # LAS DOS COSAS VAN JUNTAS A PROPOSITO. Exigir haberla visto nacer
            # nunca fue un filtro de calidad: es la UNICA forma de saber la
            # edad, porque de una moneda que ya existia solo se sabe cuanto
            # hace que se la mira. Si la edad no se mira, ese requisito no
            # protege de nada y sigue siendo el mas duro de todos: descarta
            # TODAS las monedas vivas en cada reinicio del bot.
            #
            # Lo que se pierde: se podra comprar una moneda de dos horas que
            # esta rebotando. Lo que lo sujeta es el paso 1, que exige verla
            # CRUZAR el corte de titulares -- una moneda muerta no suma gente
            # nueva-- y el paso de la presion compradora, aqui abajo.
            edad = self._edad(mint)
            if not self._edad_vale(mint, edad):
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

            # 4) NO COMPRAR EL ULTIMO ESCALON DE UNA VELA VERDE.
            #
            # EL CASO QUE LO TRAJO, 23-sep: el bot entro en CATO a 26K de
            # market cap justo despues de que la moneda subiera de 7,5K a 25K
            # en medio minuto. Medio minuto mas tarde valia 12,4K. Comprar
            # detras de una subida vertical es comprar el techo: la gente que
            # la empujo ya esta dentro y lo siguiente que hace es vender.
            #
            # NO ES LO MISMO QUE LA PRESION DE ABAJO. La presion mira si AHORA
            # entra mas dinero del que sale, y en plena vela verde la presion
            # es inmejorable -- justo en el peor momento para entrar. Esto
            # mira el PRECIO, que es lo que ya ha pasado.
            #
            # Sin datos no se bloquea: `subida()` devuelve 0 cuando no hay con
            # que comparar, y no tener datos no es un motivo para descartar.
            if self.max_subida > 0:
                _subida = volumen.subida(mint, self.subida_seg)
                if _subida > self.max_subida:
                    self._cuenta("vela_verde", mint)
                    continue

            # 5) ENTRA MAS DINERO DEL QUE SALE, AHORA MISMO.
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
            if not self._el_dinero_acompana(mint):
                continue

            # SE MARCA DESPUES DE LANZAR, NO ANTES. Si no hay hueco, la
            # moneda NO queda marcada y se vuelve a mirar en la siguiente
            # operacion suya; marcarla antes la perdia para siempre.
            if mint in self._decididas:
                continue
            # `edad` puede ser None con el corte de edad apagado: entonces no
            # se conoce y va 0 al rotulo, que es lo que significa "no se sabe".
            if self._lanzar(mint, titulares, edad or 0.0, token_callback):
                self._ya_decidida(mint)

    def _heartbeat_si_toca(self) -> None:
        if self.heartbeat_interval <= 0:
            return
        ahora = monotonic()
        if ahora - self._last_heartbeat < self.heartbeat_interval:
            return
        self._last_heartbeat = ahora
        parte = build_holders_heartbeat(
            self.min_holders, self.max_edad_seg, self.buy_amount,
            self._embudo, paper_mode=self.paper_mode,
        )
        # AL LOG SIEMPRE. El embudo es el unico diagnostico que hay para saber
        # POR QUE no se compra, y se estaba calculando para mandarlo solo a
        # Telegram... que esta apagado a peticion. O sea que se tiraba justo
        # cuando hacia falta.
        logger.info("Parte de titulares:\n%s", _sin_etiquetas(parte))
        try:
            from monitoring.notifier import notify  # noqa: PLC0415

            notify(parte)
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

    # ----------------------------------------------------------- por geyser

    async def _escuchar_geyser(
        self, program_id: str,
        token_callback: Callable[[TokenInfo], Awaitable[None]],
    ) -> None:
        """Mismo trabajo que el websocket, pero con los datos antes.

        POR QUE ES MAS RAPIDO. El websocket (`logsSubscribe`) espera a que el
        nodo RPC procese el bloque y entonces te lo empuja. Geyser sale del
        validador directamente, sin esa capa. Son tipicamente 100-300 ms de
        diferencia -- poco para casi todo, decisivo en los primeros segundos de
        una moneda.

        LO QUE NO CAMBIA: lo que se hace con los datos. Geyser trae los mismos
        `log_messages` que el websocket, asi que se los pasa al mismo
        `registrar_logs` y al mismo `_revisar`. Solo cambia la tuberia.
        """
        import grpc  # noqa: PLC0415

        from geyser.generated import geyser_pb2, geyser_pb2_grpc  # noqa: PLC0415

        if self.geyser_auth == "x-token":
            auth = grpc.metadata_call_credentials(
                lambda _, cb: cb((("x-token", self.geyser_token),), None)
            )
        else:
            auth = grpc.metadata_call_credentials(
                lambda _, cb: cb(
                    (("authorization", f"Basic {self.geyser_token}"),), None
                )
            )
        creds = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(), auth
        )
        canal = grpc.aio.secure_channel(self.geyser_endpoint, creds)
        try:
            stub = geyser_pb2_grpc.GeyserStub(canal)
            peticion = geyser_pb2.SubscribeRequest()
            peticion.transactions["pump"].account_include.append(program_id)
            # Las fallidas no dicen nada: no mueven reservas ni titulares.
            peticion.transactions["pump"].failed = False
            # PROCESSED, igual que el websocket: es el unico nivel que existe
            # a tiempo para esto. Esperar a "confirmed" son cientos de ms mas.
            peticion.commitment = geyser_pb2.CommitmentLevel.PROCESSED

            logger.info("TITULARES: escuchando por GEYSER (%s)", self.geyser_endpoint)
            async for update in stub.Subscribe(iter([peticion])):
                if not update.HasField("transaction"):
                    continue
                meta = getattr(update.transaction.transaction, "meta", None)
                logs = list(getattr(meta, "log_messages", None) or ())
                if not logs:
                    continue
                tocadas = volumen.registrar_logs(
                    logs, slot=getattr(update.transaction, "slot", None)
                )
                self._revisar(tocadas, token_callback)
                self._heartbeat_si_toca()
        finally:
            with contextlib.suppress(Exception):
                await canal.close()

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Escucha la cadena y compra las que cumplen las dos condiciones."""
        program_id = str(self._impls.event_parser.get_program_id())
        # Que el stream recuerde al menos lo que dura la ventana de compra. Sin
        # esto guardaba cinco minutos mientras la ventana era de veinte: a las
        # monedas que borraba en marcha les volvia el recuento de titulares a
        # cero y ademas quedaban descartadas para siempre por "sin historia".
        # Sin corte de edad no hay "ventana de compra" que cubrir, pero la
        # memoria sigue haciendo falta para contar titulares: se le da un rato
        # generoso para que a una moneda no le vuelva el recuento a cero.
        volumen.configurar_memoria(self.max_edad_seg or 1800.0)
        logger.info(
            "TITULARES ACTIVA: dispara con %d titulares y %s, %.4f SOL, solo "
            "pump.fun (programa %s). SIN anti-bundle.",
            self.min_holders,
            (
                f"menos de {self.max_edad_seg / 60:.0f} min de vida"
                if self.max_edad_seg > 0
                else "SIN corte de edad (tampoco hace falta verla nacer)"
            ),
            self.buy_amount, program_id,
        )
        por_geyser = bool(self.geyser_endpoint and self.geyser_token)
        if not por_geyser:
            logger.info(
                "TITULARES: escuchando por websocket. Con GEYSER_ENDPOINT y "
                "GEYSER_API_TOKEN puestos en el .env se usaria Geyser, que "
                "llega 100-300 ms antes."
            )
        espera = _ESPERA_RECONEXION
        while True:
            try:
                if por_geyser:
                    await self._escuchar_geyser(program_id, token_callback)
                    continue
                async with websockets.connect(
                    self.wss_endpoint, ping_interval=20
                ) as ws:
                    await self._suscribir(ws, program_id)
                    # Conectado Y suscrito: se vuelve a empezar de abajo.
                    espera = _ESPERA_RECONEXION
                    await self._escuchar(ws, token_callback)
            except asyncio.CancelledError:
                for tarea in list(self._en_vuelo):
                    tarea.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await tarea
                raise
            except Exception as exc:  # noqa: BLE001
                espera = self._avisa_de_la_caida(exc, espera)
                await asyncio.sleep(espera)
                espera = min(espera * 2, _ESPERA_RECONEXION_MAX)

    def _avisa_de_la_caida(self, exc: Exception, espera: float) -> float:
        """Cuenta la caida en UNA linea y dice cuanto hay que esperar.

        DOS COSAS QUE COSTARON DOS DIAS EL 22-SEP, y las dos estaban aqui:

        1. SE REINTENTABA CADA 5 SEGUNDOS PARA SIEMPRE. Cuando el nodo
           contesta 429 ("demasiadas peticiones"), reintentar 720 veces por
           hora es exactamente lo que mantiene vivo el 429: el bot se
           autobloqueaba y no volvia a conectarse nunca. Ahora la espera se
           dobla en cada intento, y un 429 empieza directamente alto porque no
           es un corte de red, es un limite de cuota que necesita tiempo.

        2. SE ESCRIBIA EL RASTRO ENTERO (`logger.exception`) en cada intento.
           Con un fallo cada 5 s eso son miles de lineas de traza por hora: el
           log quedaba ilegible desde el movil y el motivo de verdad --una
           linea de 429 al final de todo-- estaba enterrado. Ahora va una
           linea, con el porque escrito en castellano.
        """
        motivo = str(exc) or exc.__class__.__name__
        if "429" in motivo:
            espera = max(espera, _ESPERA_TRAS_429)
            logger.warning(
                "TITULARES: el nodo rechaza la conexion por EXCESO DE "
                "PETICIONES (429). No es un corte de red: es la cuota de "
                "Helius. Se espera %.0fs. Si se repite, hay que bajar "
                "`node.max_rps` o subir de plan.",
                espera,
            )
            return espera
        logger.warning(
            "TITULARES: se cayo el websocket (%s). Se reintenta en %.0fs.",
            motivo[:160], espera,
        )
        return espera
