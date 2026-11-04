"""Cuanto tarda el bot desde que ve la senal hasta que manda la compra.

LO PEDIDO: "cuando lo detecte, compra al momento".

NO se mide con un cronometro --eso dependeria de la maquina y del nodo, y un
test asi se pone rojo solo--. Se mide lo unico que de verdad manda: CUANTAS
VUELTAS AL NODO hay entre detectar y comprar. Cada vuelta a un RPC de Solana
son decenas de milisegundos, y van en serie: tres seguidas es tres veces la
espera de una.

LO QUE HABIA: `is_curve_complete()` (que por dentro solo llama a
`get_pool_state`), luego `get_pool_state()` otra vez sobre LA MISMA cuenta, y
luego `get_account_info(mint)`, que no dependia de ninguna de las dos y aun asi
esperaba su turno. Tres viajes en serie donde basta uno.
"""

# ruff: noqa: S101, ANN001, ANN202, ARG002, PLC0415, PLR2004

import asyncio
from pathlib import Path

import yaml

from trading.token_builder import build_pumpfun_token_info

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
MINT = "DBDqhnAi5MjaHsk1JyUonAtUUrmRBJPjJi3GdiBLpump"


class _Curva:
    """Curva viva y normal: con reservas y sin migrar."""

    def __init__(self) -> None:
        self.lecturas = 0
        self.orden: list[str] = []

    async def is_curve_complete(self, _pool):
        msg = "lectura duplicada de la curva"
        raise AssertionError(msg)

    async def get_pool_state(self, _pool, commitment=None, *, exigir_precio=True):
        self.lecturas += 1
        self.orden.append("curva:empieza")
        # Cede el control: si el otro viaje se lanzo a la vez, entra AQUI.
        await asyncio.sleep(0)
        self.orden.append("curva:acaba")
        return {
            "complete": False,
            "creator": "11111111111111111111111111111111",
            "is_mayhem_mode": False,
            "is_cashback_coin": False,
            "price_per_token": 0.000000031,
            "virtual_token_reserves": 1_000_000_000_000,
            "virtual_sol_reserves": 30_000_000_000,
            "token_total_supply": 1_000_000_000_000_000,
        }


class _Cliente:
    def __init__(self, orden: list[str]) -> None:
        self.orden = orden
        self.lecturas = 0
        self.commitments: list[str | None] = []

    async def get_account_info(self, _mint, commitment=None):
        self.lecturas += 1
        self.commitments.append(commitment)
        self.orden.append("mint:empieza")
        await asyncio.sleep(0)
        self.orden.append("mint:acaba")
        return type("Cuenta", (), {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"})()


class _Impls:
    def __init__(self, curva) -> None:
        self.curve_manager = curva
        self.address_provider = type(
            "AP", (), {
                "derive_pool_address": staticmethod(lambda _m: object()),
                "get_additional_accounts": staticmethod(lambda _t: {}),
            },
        )()


def _construye() -> tuple[_Curva, _Cliente]:
    curva = _Curva()
    orden: list[str] = []
    cliente = _Cliente(orden)
    curva.orden = orden
    asyncio.run(
        build_pumpfun_token_info(MINT, _Impls(curva), cliente, permitir_migradas=False)
    )
    return curva, cliente


def test_la_curva_se_lee_una_sola_vez() -> None:
    """Dos lecturas de la misma cuenta es un viaje al nodo tirado a la basura.

    El `is_curve_complete` de arriba salta si alguien lo vuelve a llamar: no
    hace falta, porque el estado que se lee ya trae el campo `complete`.
    """
    curva, _ = _construye()
    assert curva.lecturas == 1


def test_la_curva_y_el_mint_se_piden_a_la_vez() -> None:
    """No dependen el uno del otro, asi que no tienen por que esperarse.

    Si fueran en serie, el orden seria empieza/acaba, empieza/acaba. Yendo a la
    vez, los dos empiezan antes de que acabe ninguno.
    """
    curva, cliente = _construye()
    assert cliente.lecturas == 1
    assert cliente.commitments == ["processed"]
    empiezan = curva.orden[:2]
    assert set(empiezan) == {"curva:empieza", "mint:empieza"}, (
        f"van en serie, no a la vez: {curva.orden}"
    )


def test_no_hay_esperas_artificiales_antes_de_comprar() -> None:
    """La pausa "para que se estabilice la curva" tiene que estar a cero.

    Es un `asyncio.sleep` literal entre detectar y comprar: con el valor por
    defecto (15 s) la moneda que se detecta a los 90 titulares se compraria
    quince segundos despues, que en pump.fun es otra moneda distinta.
    """


    cfg = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml")
        .read_text(encoding="utf-8")
    )
    assert cfg.get("retries", {}).get("wait_after_creation", 15) == 0
    assert cfg["filters"]["max_buy_age"] == 0
    assert cfg.get("retries", {}).get("wait_before_new_token", 0) == 0


# ------------------------------------- lo que pasa JUSTO despues de comprar


def test_el_aviso_de_compra_no_llama_a_la_api_de_devs() -> None:
    """DOCE SEGUNDOS DE CEGUERA en el peor momento posible.

    Aqui habia un `await creator_migration_stats(dev)`: una llamada HTTP a la
    API de pump.fun con `ClientTimeout(total=12)`, puesta en el camino critico
    entre que la compra confirma y que arranca el monitor de la posicion.
    Durante esos segundos la moneda recien comprada no la mira nadie -- ni take
    profit ni stop loss. En una moneda que hace x3,75 en un minuto, doce
    segundos es media subida.

    Y ademas era medir un dev, que Manu pidio quitar el 19-sep. Las dos razones
    apuntan al mismo sitio.

    El test mira el bloque del aviso de compra, no el fichero entero: la
    llamada sigue siendo legitima en el camino del filtro `pumpfun_dev`, que
    esta apagado y es una decision distinta.
    """
    fuente = (
        Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"
    ).read_text(encoding="utf-8")
    inicio = fuente.index("AVISO DE COMPRA con el porque")
    # Solo CODIGO: el comentario que explica por que se quito nombra la
    # llamada, y sin esto el test se tropezaria con su propia explicacion.
    codigo = [
        ln for ln in fuente[inicio:inicio + 4000].split("\n")
        if not ln.lstrip().startswith("#")
    ]
    assert "await creator_migration_stats" not in "\n".join(codigo), (
        "vuelve a haber una llamada bloqueante a la API de devs en el aviso "
        "de compra"
    )


def test_la_comision_de_compra_llega_como_override() -> None:
    """Si no se pasa, la compra pagaria la comision barata de las ventas."""
    fuente = (
        Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"
    ).read_text(encoding="utf-8")
    assert "_fee_override = self.fee_compra or None" in fuente
    assert "priority_fee_override=_fee_override" in fuente


def test_se_apunta_cuanto_tardo_la_compra_en_entrar() -> None:
    """EL DATO QUE FALTABA el 19-sep.

    Una moneda se compro despues de haberse desplomado, y para saber si fue
    mala idea o simplemente llego tarde habia que restar a mano las horas de
    dos lineas distintas del log, desde el movil. Ahora la linea de la compra
    lo dice sola.
    """
    fuente = Path(__file__).resolve().parent.parent
    holders = (fuente / "src/monitoring/holders_source.py").read_text(encoding="utf-8")
    trader = (fuente / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    assert '"decidida_en": monotonic()' in holders, "la fuente no apunta la hora"
    assert '"decidida_en"' in trader, "el trader no la lee"
    assert "en entrar" in trader, "no se imprime la tardanza"


def test_el_precio_de_la_curva_no_se_lee_dos_veces() -> None:
    """UN VIAJE AL NODO MENOS entre detectar la moneda y firmar la compra.

    `token_builder` lee la cuenta de la curva para construir la moneda, y el
    comprador la volvia a leer milisegundos despues -- la MISMA cuenta. Con la
    moneda moviendose, cada viaje al nodo es precio que se escapa.

    Ahora el constructor deja el precio con su hora y el comprador lo reutiliza
    si sigue fresco.
    """
    raiz = Path(__file__).resolve().parent.parent
    builder = (raiz / "src/trading/token_builder.py").read_text(encoding="utf-8")
    comprador = (raiz / "src/trading/platform_aware.py").read_text(encoding="utf-8")
    assert '"precio_curva"' in builder, "el constructor no deja el precio"
    assert '"precio_curva_en"' in builder, "sin la hora no se puede saber si vale"
    assert "_precio_ya" in comprador, "el comprador no lo aprovecha"


def test_el_precio_reutilizado_tiene_caducidad() -> None:
    """Un precio viejo no se usa JAMAS: si el camino se atasca, se relee.

    Sin caducidad esto seria comprar a ciegas con un precio de hace quien sabe
    cuanto, que es exactamente el fallo que se esta intentando arreglar.
    """
    from trading.platform_aware import _PRECIO_FRESCO_SEG

    assert 0 < _PRECIO_FRESCO_SEG <= 5


def test_la_compra_no_se_simula_y_la_venta_si() -> None:
    """LA ASIMETRIA, y el porqué de cada lado.

    COMPRAR: se compite por entrar. La simulación previa es otro viaje al nodo
    (100-300 ms) y llegar tarde es lo que ha costado dinero. Va sin simular.

    VENDER: no hay carrera -- la posición ya es nuestra. Ahí la simulación sale
    a cuenta: una venta que iba a reventar se rechaza sin enviarse y sale
    gratis. El 1-ago, sin ella, 178 ventas fallidas quemaron 0,64 SOL en
    comisiones.

    Este test existe porque las dos líneas están en el MISMO fichero y a 300
    líneas de distancia: es fácil "uniformarlas" sin darse cuenta de que son
    distintas a propósito.
    """
    fuente = (
        Path(__file__).resolve().parent.parent / "src/trading/platform_aware.py"
    ).read_text(encoding="utf-8")
    # Solo CODIGO: los comentarios que explican cada decision nombran las dos
    # opciones, y sin esto el test se contaria a si mismo.
    codigo = [
        ln.strip() for ln in fuente.split("\n")
        if "skip_preflight=" in ln and not ln.lstrip().startswith("#")
    ]
    assert codigo == ["skip_preflight=True,", "skip_preflight=False,"], (
        f"se esperaba comprar sin simular y vender simulado; hay: {codigo}"
    )


def test_queda_escrito_lo_que_cuesta_no_simular() -> None:
    """Sin simulación, una compra que revierte aterriza y paga igual. Con la
    comisión subida a ~0,22 US$ eso ya no es calderilla, y quien lea esto
    dentro de tres meses tiene que encontrarlo escrito."""
    fuente = (
        Path(__file__).resolve().parent.parent / "src/trading/platform_aware.py"
    ).read_text(encoding="utf-8")
    assert "LO QUE CUESTA" in fuente


# ---------------------------------------- la tuberia por la que entran datos


def test_la_fuente_puede_escuchar_por_geyser() -> None:
    """LO QUE DE VERDAD ATACA "LOS PRIMEROS SEGUNDOS".

    El websocket (`logsSubscribe`) espera a que el nodo RPC procese el bloque y
    entonces lo empuja. Geyser sale del validador directamente, sin esa capa:
    tipicamente 100-300 ms antes. Poco para casi todo, decisivo en los primeros
    segundos de una moneda.

    Lo que NO cambia es el tratamiento: Geyser trae los mismos `log_messages`
    que el websocket, asi que van al mismo `registrar_logs` y al mismo
    `_revisar`. Solo cambia la tuberia.
    """
    fuente = (
        Path(__file__).resolve().parent.parent / "src/monitoring/holders_source.py"
    ).read_text(encoding="utf-8")
    assert "_escuchar_geyser" in fuente
    assert "log_messages" in fuente
    # Y por el mismo sitio que el websocket, o serian dos caminos que mantener.
    assert fuente.count("volumen.registrar_logs(") == 2


def test_sin_credenciales_de_geyser_se_sigue_usando_el_websocket() -> None:
    """No puede exigir configuracion nueva para seguir funcionando: una maquina
    sin esas credenciales tiene que arrancar igual que antes."""
    fuente = (
        Path(__file__).resolve().parent.parent / "src/monitoring/holders_source.py"
    ).read_text(encoding="utf-8")
    assert "por_geyser = bool(self.geyser_endpoint and self.geyser_token)" in fuente


def test_las_credenciales_no_pasan_por_el_yaml() -> None:
    """SI EL YAML LAS PIDIERA Y NO ESTUVIERAN, EL BOT NO ARRANCARIA.

    `resolve_env_vars` LANZA cuando una variable `${...}` no existe, asi que
    poner `${GEYSER_ENDPOINT}` en el yaml dejaria sin arrancar a cualquier
    maquina que no tenga esa linea en su `.env`. Se leen con `os.getenv` y un
    valor por defecto, que no puede romper nada.
    """
    runner = (
        Path(__file__).resolve().parent.parent / "src/bot_runner.py"
    ).read_text(encoding="utf-8")
    assert 'os.getenv("GEYSER_ENDPOINT", "")' in runner
    yaml_txt = CONFIG.read_text(encoding="utf-8")
    assert "${GEYSER" not in yaml_txt
