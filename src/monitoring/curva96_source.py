"""Fuente de compra: entrar cuando la curva de pump.fun va casi llena.

Por que esto existe. Las demas fuentes esperan a que alguien de fuera diga algo
(DexScreener cobrado, un boost, DEXTools, un canal de Telegram). Eso llega
tarde y solo para una parte de las monedas. El avance de la bonding curve, en
cambio, es un hecho de la cadena: cuando queda un 4% para llenarse, la moneda
esta a punto de migrar a PumpSwap, y la migracion es el momento en que entra la
gente que no mira pump.fun.

De donde sale el dato, y por que es gratis. Cada operacion de pump.fun emite un
``TradeEvent`` que incluye las reservas de la curva DESPUES de ejecutarse. El
bot ya lee ese stream entero para contar volumen, asi que saber por donde va
cada curva no cuesta ni una llamada al nodo. La alternativa -- preguntar el
estado de la curva moneda por moneda -- serian miles de llamadas por vuelta
contra un presupuesto de 15 por segundo: no cabe.

Por que esta fuente se suscribe por su cuenta. Cuando hay fuentes de
adquisicion activas, el trader NO arranca el listener de creaciones (ver
`UniversalTrader.start`), asi que nadie estaria alimentando `volumen`. Esta
fuente abre su propio `logsSubscribe` y se alimenta sola.

Lo que NO compra: bundles de pump and dump. Un bundle se monta en el bloque de
creacion -- varias carteras del mismo dueno comprando a la vez que nace la
moneda, para tener el token a precio cero y soltarlo sobre quien entre despues.
Eso se ve en el stream sin preguntar a nadie, PERO solo si se estaba mirando
cuando nacio; por eso por defecto se exige haber visto la creacion, y las que
ya estaban en marcha cuando arranco el bot se dejan pasar de largo en vez de
comprarlas a ciegas.
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

#: Cuantas monedas se recuerdan como "ya probada". Solo hace falta que dure lo
#: que tarda una moneda en migrar; con esto sobra de largo.
_MAX_RECORDADAS = 20_000

#: Evaluaciones simultaneas. Cada una hace unas pocas llamadas al nodo para
#: reconstruir la moneda; sin tope, una racha de migraciones a la vez se comeria
#: el presupuesto de RPC que necesitan los stop loss de lo ya comprado.
_MAX_EN_VUELO = 4


def build_curva96_heartbeat(
    umbral: float,
    buy_amount: float,
    embudo: dict[str, int],
    *,
    paper_mode: bool,
) -> str:
    """Mensaje periodico de salud para Telegram.

    Muestra el embudo entero, no solo lo que sale por abajo: "0 compradas"
    puede ser que no llegue nada o que llegue y se caiga todo por el camino, y
    son problemas opuestos.
    """
    modo = "PAPER" if paper_mode else "REAL"
    return "\n".join(
        [
            "🎯 <b>CURVA CASI LLENA</b>",
            f"Disparo: curva ≥ <b>{umbral * 100:.0f}%</b> · {buy_amount:g} SOL",
            f"👀 Monedas con movimiento: <b>{embudo.get('vistas', 0)}</b>",
            f"  ↳ llegaron al {umbral * 100:.0f}%: {embudo.get('en_umbral', 0)}",
            f"  ↳ sin ver su creación: {embudo.get('sin_historia', 0)}",
            f"  ↳ bundle descartado: {embudo.get('bundles', 0)}",
            f"  ↳ pocos compradores: {embudo.get('poca_gente', 0)}",
            f"  ↳ sin presión reciente: {embudo.get('sin_presion', 0)}",
            f"  ↳ vela 1s descartada: {embudo.get('vela_1s', 0)}",
            f"  ↳ no se pudo construir: {embudo.get('sin_construir', 0)}",
            f"✅ Enviadas a comprar: <b>{embudo.get('comprables', 0)}</b>",
            f"Modo: <b>{modo}</b>",
        ]
    )


class Curva96Source:
    """Vigila el avance de las curvas y compra las que llegan al umbral."""

    def __init__(  # noqa: PLR0913
        self,
        wss_endpoint: str,
        platform_impls: object,
        client: object,
        *,
        umbral: float = 0.96,
        tope: float = 0.995,
        buy_amount: float = 0.2,
        max_carteras_primer_bloque: int = 3,
        max_sol_primer_bloque: float = 2.0,
        min_compradores: int = 30,
        min_edad_seg: float = 0.0,
        max_edad_seg: float = 0.0,
        min_compradores_ola: int = 8,
        ventana_presion: float = 3.0,
        min_presion_ratio: float = 2.0,
        min_compra_sol: float = 1.0,
        vela_seg: float = 1.0,
        max_subida_vela: float = 0.35,
        max_bundle_pct: float = 0.5,
        max_bundle_pct_sin_historia: float = 0.35,
        exigir_ver_creacion: bool = True,
        heartbeat_interval: float = 300,
        paper_mode: bool = False,
    ) -> None:
        self.wss_endpoint = wss_endpoint
        self._impls = platform_impls
        self._client = client
        self.umbral = float(umbral)
        self.tope = float(tope)
        self.buy_amount = float(buy_amount)
        self.max_carteras_primer_bloque = int(max_carteras_primer_bloque)
        self.max_sol_primer_bloque = float(max_sol_primer_bloque)
        self.min_compradores = int(min_compradores)
        # VENTANA DE EDAD DE LA MONEDA, pedida el 11-oct: "que pasen las de 15
        # segundos hasta 3h". 0 en cualquiera de las dos = ese lado no se mira.
        self.min_edad_seg = max(0.0, float(min_edad_seg or 0))
        self.max_edad_seg = max(0.0, float(max_edad_seg or 0))
        self.min_compradores_ola = int(min_compradores_ola)
        self.ventana_presion = float(ventana_presion)
        self.min_presion_ratio = float(min_presion_ratio)
        self.min_compra_sol = float(min_compra_sol)
        self.vela_seg = float(vela_seg)
        self.max_subida_vela = float(max_subida_vela)
        self.max_bundle_pct = float(max_bundle_pct)
        # MAS APRETADO CUANDO SE VA A CIEGAS. Si no se vio nacer la moneda,
        # los avisos del bloque de creacion no existen y este tope en cadena
        # se queda casi solo, asi que tiene que pedir mas.
        self.max_bundle_pct_sin_historia = float(max_bundle_pct_sin_historia)
        self.exigir_ver_creacion = bool(exigir_ver_creacion)
        self.heartbeat_interval = float(heartbeat_interval)
        self.paper_mode = bool(paper_mode)

        self._probadas: set[str] = set()
        self._en_vuelo: set[asyncio.Task] = set()
        self._last_heartbeat: float = 0.0
        self._embudo: dict[str, int] = {}

    # ------------------------------------------------------------------ datos

    def _cuenta(self, clave: str) -> None:
        self._embudo[clave] = self._embudo.get(clave, 0) + 1

    def _ya_probada(self, mint: str) -> bool:
        """True si esta moneda ya se miro. Solo se decide UNA vez por moneda.

        Sin esto, una moneda que cruza el umbral lo seguiria cruzando en cada
        operacion posterior y se intentaria comprar cientos de veces.
        """
        if mint in self._probadas:
            return True
        if len(self._probadas) >= _MAX_RECORDADAS:
            self._probadas.clear()
        self._probadas.add(mint)
        return False

    # ----------------------------------------------------------------- bundle

    def _es_bundle(self, mint: str, symbol: str, *, con_historia: bool) -> bool:
        """True si la moneda tiene pinta de bundle de pump and dump.

        Todo sale del stream de la cadena, sin preguntar a ningun indexador:
        quien compro en el bloque de creacion y cuanta gente distinta ha
        comprado desde entonces.

        Args:
            con_historia: Si se vio nacer la moneda. Sin eso, los dos primeros
                avisos no se pueden comprobar: sus contadores valen CERO y
                pasarian solos, que es peor que no mirarlos --daria un visto
                bueno que nadie ha dado. Asi que se saltan a proposito y el
                peso recae en el recuento de compradores, que si funciona con
                historia parcial, y en el tope de concentracion en cadena, que
                para estas va mas apretado.
        """
        if not con_historia:
            logger.info(
                "%s (%s): no se vio nacer, así que el bloque de creación no "
                "se puede mirar. Deciden los compradores distintos y la "
                "concentración en cadena.",
                symbol,
                mint,
            )
            return self._poca_gente(mint, symbol)

        carteras = volumen.carteras_primer_bloque(mint)
        if (
            self.max_carteras_primer_bloque > 0
            and carteras > self.max_carteras_primer_bloque
        ):
            logger.info(
                "BUNDLE descartado %s (%s): %d carteras compraron en el bloque "
                "de creación (máx %d)",
                symbol,
                mint,
                carteras,
                self.max_carteras_primer_bloque,
            )
            self._cuenta("bundles")
            return True

        sol_inicial = volumen.sol_primer_bloque(mint)
        if self.max_sol_primer_bloque > 0 and sol_inicial > self.max_sol_primer_bloque:
            logger.info(
                "BUNDLE descartado %s (%s): %.2f SOL cargados en el bloque de "
                "creación (máx %.2f)",
                symbol,
                mint,
                sol_inicial,
                self.max_sol_primer_bloque,
            )
            self._cuenta("bundles")
            return True

        return self._poca_gente(mint, symbol)

    def _poca_gente(self, mint: str, symbol: str) -> bool:
        """True si a la moneda la ha comprado muy poca gente distinta.

        Es el unico aviso del stream que sigue valiendo sin haber visto nacer
        la moneda: cuenta las carteras vistas desde que se la mira, asi que se
        queda corto pero nunca de mas. Una curva casi llena movida por cuatro
        carteras no es demanda, es un grupo empujando.
        """
        if self.min_compradores <= 0:
            return False
        compradores = int(volumen.metricas_segunda_ola(mint).get("compradores", 0) or 0)
        if compradores < self.min_compradores:
            logger.info(
                "Descartada %s (%s): solo %d compradores distintos para una "
                "curva casi llena (mín %d) — la subió un grupo pequeño",
                symbol,
                mint,
                compradores,
                self.min_compradores,
            )
            self._cuenta("poca_gente")
            return True
        return False

    def _fuera_de_edad(self, mint: str, symbol: str) -> bool:
        """True si la moneda es demasiado nueva o demasiado vieja.

        PEDIDO EL 11-oct: "que pasen las de 15 segundos hasta 3h", despues de
        "asegurate que no es mecha de pump y dump". Las dos puntas descartan
        cosas distintas:

          - DEMASIADO NUEVA. Una moneda que ha recorrido el 95% de su curva
            --un x11,3-- en menos de 15 segundos no ha tenido tiempo de que la
            compre nadie de verdad: es un bundle o una mecha. El freno de la
            vela verde mira el PRECIO; este mira el RELOJ, y llega incluso
            cuando no hay suficientes puntos de precio para medir la subida.
          - DEMASIADO VIEJA. Una moneda que lleva horas pegada al 95% sin
            migrar no esta subiendo: esta atascada. Lo que queda no es el
            tiron, es el aburrimiento.

        SIN EDAD NO SE PUEDE ASEGURAR NADA, y eso descarta. Si el bot no vio
        nacer la moneda, `volumen.edad()` devuelve None y esta puerta no puede
        saber si cae dentro de la ventana. Dejarla pasar seria el fallo de
        siempre de este repo: un freno encendido, configurado y mudo justo
        para las monedas que no puede medir (FUCKETHAN, -91,9%). Asi que se
        descarta y se cuenta en el embudo, para que el panel diga cuantas se
        caen aqui en vez de que desaparezcan en silencio.

        EL PREFIJO DEL LOG ES "Evitado" A PROPOSITO, y no "Descartada" como
        las puertas vecinas de este mismo fichero: `deploy/porque.sh` junta
        los motivos con `grep -oE "Evitado [^:]+: .*"`, asi que una linea que
        empiece por "Descartada" NO sale en el diagnostico. Esta puerta puede
        descartar MUCHO, asi que tiene que verse con `porque`.
        (Las de los vecinos no salen -- es un hueco anterior, avisado.)

        OJO CON LA INTERACCION: con `exigir_ver_creacion: False` la fuente
        acepta monedas sin historia, pero esta puerta las rechaza igual
        mientras haya una edad pedida. O sea que poner una ventana de edad
        aprieta MAS que lo que sugiere ese `False`. Poner las dos a 0 lo
        deshace.
        """
        if self.min_edad_seg <= 0 and self.max_edad_seg <= 0:
            return False

        edad = volumen.edad(mint)
        if edad is None:
            if self.max_edad_seg > 0:
                ventana = (
                    f"entre {self.min_edad_seg:.0f}s y "
                    f"{self.max_edad_seg:.0f}s"
                )
            else:
                ventana = f"con al menos {self.min_edad_seg:.0f}s, sin máximo"
            logger.info(
                "Evitado %s (%s): no se vio nacer, así que no se puede "
                "saber su edad — se pidió comprar %s",
                symbol,
                mint,
                ventana,
            )
            self._cuenta("edad_desconocida")
            return True

        if self.min_edad_seg > 0 and edad < self.min_edad_seg:
            logger.info(
                "Evitado %s (%s): solo %.1fs de vida y ya al 95%% de curva "
                "(mín %.0fs) — eso no lo sube gente, es una mecha",
                symbol,
                mint,
                edad,
                self.min_edad_seg,
            )
            self._cuenta("demasiado_nueva")
            return True

        if self.max_edad_seg > 0 and edad > self.max_edad_seg:
            logger.info(
                "Evitado %s (%s): %.0f min de vida pegada a la curva "
                "(máx %.0f min) — lleva horas sin migrar, está atascada",
                symbol,
                mint,
                edad / 60,
                self.max_edad_seg / 60,
            )
            self._cuenta("demasiado_vieja")
            return True

        return False

    # ---------------------------------------------------------------- decidir

    def _sin_mecha_vertical(self, mint: str, symbol: str) -> bool:
        """Rechaza únicamente una subida vertical dentro de la vela corta."""
        subida = volumen.subida(mint, self.vela_seg)
        if self.max_subida_vela > 0 and subida > self.max_subida_vela:
            logger.info(
                "Descartada %s (%s): vela de +%.0f%% en %.1fs; posible "
                "pump and dump",
                symbol,
                mint,
                subida * 100,
                self.vela_seg,
            )
            self._cuenta("vela_1s")
            return False
        return True

    async def _evaluar(
        self, mint: str, token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        """Comprueba una moneda que acaba de cruzar el umbral y la manda a comprar."""
        avance = volumen.progreso_curva(mint)
        if avance is None:
            return
        progreso, sol_curva = avance

        # No persigue una subida vertical de un segundo. Fuera de esa mecha,
        # la entrada ya no exige presión reciente ni un volumen mínimo en SOL.
        if not self._sin_mecha_vertical(mint, mint[:6]):
            self._probadas.discard(mint)
            return

        con_historia = volumen.vio_la_creacion(mint)
        if not con_historia:
            self._cuenta("sin_historia")
            if self.exigir_ver_creacion:
                logger.info(
                    "Curva al %.1f%% en %s pero no se vio nacer (el bot "
                    "arrancó después): no se puede saber si es un bundle, se "
                    "deja pasar",
                    progreso * 100,
                    mint,
                )
                return

        # LA EDAD TAMBIEN ANTES DE RECONSTRUIR, por lo mismo: sale de memoria
        # y no cuesta una sola llamada al nodo.
        if self._fuera_de_edad(mint, mint[:6]):
            return

        # El bundle se mira ANTES de reconstruir la moneda: lo de arriba es
        # memoria y no cuesta nada, y reconstruirla son varias llamadas al nodo
        # que no hay por que gastar en algo que se va a descartar igual.
        if self._es_bundle(mint, mint[:6], con_historia=con_historia):
            return

        token_info = await build_pumpfun_token_info(
            mint, self._impls, self._client, permitir_migradas=False
        )
        if token_info is None:
            self._cuenta("sin_construir")
            return

        symbol = token_info.symbol or mint[:6]

        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "acquisition_source": "curva96",
            "origen": "curva96",
            "signal_buy_amount": self.buy_amount,
            # Freno adicional EN CADENA, que no depende de haber visto nacer la
            # moneda: si los mayores titulares concentran mas de esto, es un
            # bundle aunque el bloque de creacion pareciera limpio.
            "max_bundle_pct": (
                self.max_bundle_pct
                if con_historia
                else self.max_bundle_pct_sin_historia
            ),
            "curva_progreso": progreso,
            "estrategia": "curva_95_sin_mecha_1s",
        }
        self._cuenta("comprables")
        # NO SE ANUNCIA UN IMPORTE QUE ESTA FUENTE NO DECIDE.
        #
        # `buy_amount` de la curva esta a 0 a proposito: quien decide el
        # tamano es `fraccion_saldo` en el trader, que lo recalcula desde el
        # saldo recien leido y bajo el cerrojo de gasto. Esta linea imprimia
        # `self.buy_amount` igualmente, o sea "se compra con 0.0000 SOL" en
        # cada moneda candidata.
        #
        # LO QUE COSTO (25-sep): esa linea en el log parecia la explicacion de
        # por que el bot no compraba -- un importe a cero-- cuando el importe
        # de verdad era el 50% de la cartera y el problema estaba en otro
        # sitio. Un log que miente manda a buscar el fallo donde no esta. Es
        # el mismo fallo que ya se corrigio en la linea de "TRAS SL: compra
        # reducida" del trader, y por las mismas razones.
        if self.buy_amount > 0:
            _importe = f"se compra con {self.buy_amount:.4f} SOL"
        else:
            _importe = "el importe lo decide el trader (% del saldo)"
        logger.info(
            "CURVA AL %.1f%% — %s (%s): %.1f SOL en la curva, %s",
            progreso * 100,
            symbol,
            mint,
            sol_curva,
            _importe,
        )
        await token_callback(token_info)

    def _lanzar(
        self, mint: str, token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        """Evalua la moneda aparte para no parar la lectura del websocket.

        Reconstruir la moneda son varias llamadas al nodo. Hacerlas dentro del
        bucle de recepcion dejaria de leer logs mientras tanto, y lo que se
        pierde ahi son justo las operaciones de las demas curvas.
        """
        if len(self._en_vuelo) >= _MAX_EN_VUELO:
            logger.warning(
                "Curva casi llena: %s se descarta, ya hay %d evaluaciones en "
                "marcha (el nodo no da para más)",
                mint,
                len(self._en_vuelo),
            )
            return

        async def _corre() -> None:
            try:
                await self._evaluar(mint, token_callback)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Fallo evaluando la curva de %s", mint)

        tarea = asyncio.create_task(_corre())
        self._en_vuelo.add(tarea)
        tarea.add_done_callback(self._en_vuelo.discard)

    def _revisar(
        self, mints: list[str], token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        """Mira solo las monedas que acaban de operar, no las miles en memoria."""
        for mint in mints:
            self._cuenta("vistas")
            avance = volumen.progreso_curva(mint)
            if avance is None:
                continue
            progreso = avance[0]
            if progreso < self.umbral or progreso > self.tope:
                continue
            self._cuenta("en_umbral")
            if self._ya_probada(mint):
                continue
            self._lanzar(mint, token_callback)

    # -------------------------------------------------------------- heartbeat

    def _heartbeat_si_toca(self) -> None:
        if self.heartbeat_interval <= 0:
            return
        ahora = monotonic()
        if (
            self._last_heartbeat
            and ahora - self._last_heartbeat < self.heartbeat_interval
        ):
            return
        self._last_heartbeat = ahora
        parte = build_curva96_heartbeat(
            self.umbral,
            self.buy_amount,
            self._embudo,
            paper_mode=self.paper_mode,
        )
        # AL LOG SIEMPRE, no solo a Telegram. Los avisos estan apagados a
        # peticion, asi que mandarlo unicamente alli es calcular el unico
        # diagnostico que existe para tirarlo. Paso exactamente lo mismo con
        # la fuente de titulares el 19-sep y costo dos dias de ceros sin poder
        # saber por que.
        logger.info(
            "Parte de la curva:\n%s",
            parte.replace("<b>", "").replace("</b>", ""),
        )
        try:
            from monitoring.notifier import notify  # noqa: PLC0415

            notify(parte)
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo enviar el latido de curva casi llena")
        self._embudo = {}

    # ------------------------------------------------------------------- ciclo

    async def _suscribir(self, websocket: object, program_id: str) -> None:
        await websocket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [program_id]},
                        {"commitment": "processed"},
                    ],
                }
            )
        )
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
            logs = resultado.get("value", {}).get("logs", [])
            slot = resultado.get("context", {}).get("slot")
            tocadas = volumen.registrar_logs(logs, slot=slot)
            self._revisar(tocadas, token_callback)
            self._heartbeat_si_toca()

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Escucha la cadena y compra las curvas que llegan al umbral."""
        program_id = str(self._impls.event_parser.get_program_id())
        logger.info(
            "Curva casi llena ACTIVA: dispara al %.0f%% de la curva con %.4f "
            "SOL, solo pump.fun (programa %s)",
            self.umbral * 100,
            self.buy_amount,
            program_id,
        )
        while True:
            try:
                async with websockets.connect(self.wss_endpoint) as websocket:
                    await self._suscribir(websocket, program_id)
                    logger.info("Suscrito a los logs de pump.fun para ver las curvas")
                    await self._escuchar(websocket, token_callback)
            except asyncio.CancelledError:
                for tarea in list(self._en_vuelo):
                    tarea.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await tarea
                raise
            except Exception:
                logger.exception("Curva casi llena: se cayó el websocket")
            logger.info("Curva casi llena: reconectando en 5 s")
            await asyncio.sleep(5)
