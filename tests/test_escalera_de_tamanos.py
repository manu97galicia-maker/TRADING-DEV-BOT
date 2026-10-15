"""La escalera de tamanos: 33% la primera, 40% la segunda, 50% la tercera.

PEDIDO (15-oct, palabras de Manu): "33% del portfolio de sol por operacion y
el 40% db la segunda y jn 50% de la restante a la tercer maximo 3 posiciones
abiertas a la vez y minimo 0.015 sol de reservar".

OJO: EL C123 YA NO USA LA ESCALERA. Ese mismo dia Manu la cambio por un 60%
plano ("pon 60% sol balance por operaciones maximo 3 simultaneamente"), asi
que `fracciones_saldo` se borro de su yaml. ESTE FICHERO SIGUE ENTERO porque
el MECANISMO sigue en el codigo y cualquier bot puede volver a usarlo: lo que
vigila es que la escalera funcione cuando se configure, no que el C123 la
tenga. Las comprobaciones del C123 se han movido a lo que lleva ahora.

POR QUE HIZO FALTA CODIGO NUEVO Y NO SOLO UN NUMERO EN EL YAML.
`fraccion_saldo` es UN escalar. Con `fraccion_base: libre` las compras ya
menguaban solas --el porcentaje se aplica al saldo que queda-- pero el
PORCENTAJE era siempre el mismo: 33% · 33% · 33% da 0,330 · 0,221 · 0,148.
Manu pide que el porcentaje SUBA: 0,330 · 0,268 · 0,201. No se puede expresar
con un escalar, asi que se anadio `fracciones_saldo` y
`_fraccion_de_esta_compra()`.

LAS DOS TRAMPAS QUE VIGILA ESTE FICHERO:

1. QUE POSICION ES ESTA se cuenta con los MARCADORES EN DISCO y no con el
   semaforo. Tras un reinicio el semaforo se recrea vacio y diria "cero
   abiertas" habiendo posiciones vivas: la siguiente compra usaria el 33% de
   un saldo YA comprometido. Es el mismo tipo de fallo que `max_posiciones: 0`
   significando 40.

2. LA RAMA `libre` EXIGIA `fraccion_saldo > 0`. Poniendo solo la escalera y
   dejando el escalar en 0, esa rama no se habria ejecutado y el importe
   habria caido al `buy_amount` fijo de 0,10 SOL -- escalera puesta y muda,
   que es justo lo que la REGLA 0 lleva escrito tres veces.
"""

# ruff: noqa: S101, SLF001, PLR2004, PLC0415

import json
from pathlib import Path

import pytest
import yaml

from trading.universal_trader import UniversalTrader

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
C123 = RAIZ / "bots/bot-unica123-real.yaml"

ESCALERA_PEDIDA = [0.33, 0.40, 0.50]
RESERVA_PEDIDA = 0.015
PLAZAS_PEDIDAS = 3


def _trader(
    tmp_path: Path, escalera: list | None, escalar: float = 0.0
) -> UniversalTrader:
    """Un trader con lo justo para elegir el peldano. Sin red ni wallet."""
    t = UniversalTrader.__new__(UniversalTrader)
    t._open_pos_path = tmp_path / "open_position.json"
    t.fraccion_saldo = escalar
    # SIN RECORTAR A PROPOSITO. El recorte es trabajo de `__init__`, y si este
    # helper lo hiciera, el test del recorte estaria probando el helper en vez
    # del bot. Se destapo al mutar el codigo: quitar el recorte de produccion
    # dejaba los 16 tests VERDES.
    t.fracciones_saldo = list(escalera or [])
    return t


def _abre(tmp_path: Path, cuantas: int) -> None:
    """Deja `cuantas` posiciones abiertas, como marcadores en disco."""
    for i in range(cuantas):
        (tmp_path / f"open_position_{i:03d}.json").write_text(
            json.dumps({"mint": f"M{i}", "value_sol": 0.1}), encoding="utf-8"
        )


# --------------------------------------------------- elegir el peldano
def test_cada_compra_usa_su_peldano(tmp_path: Path) -> None:
    t = _trader(tmp_path, ESCALERA_PEDIDA)
    for abiertas, esperada in ((0, 0.33), (1, 0.40), (2, 0.50)):
        for f in tmp_path.glob("open_position_*.json"):
            f.unlink()
        _abre(tmp_path, abiertas)
        frac, peldano = t._fraccion_de_esta_compra()
        assert frac == pytest.approx(esperada), f"con {abiertas} abiertas"
        assert peldano == abiertas


def test_con_mas_posiciones_que_peldanos_se_repite_el_ultimo(tmp_path: Path) -> None:
    """No se cae al escalar ni se levanta por indice.

    Con `max_posiciones` por encima de los peldanos escritos, la compra de
    mas usa el porcentaje MAS ALTO. Sobre un saldo ya mermado eso es el lado
    seguro del error: pide mucho de poco, no mucho de mucho.
    """
    t = _trader(tmp_path, ESCALERA_PEDIDA, escalar=0.99)
    _abre(tmp_path, 7)
    frac, peldano = t._fraccion_de_esta_compra()
    assert frac == pytest.approx(0.50)
    assert peldano == len(ESCALERA_PEDIDA) - 1


def test_sin_escalera_manda_el_escalar_de_siempre(tmp_path: Path) -> None:
    """Quitar la escalera tiene que devolver el comportamiento anterior."""
    t = _trader(tmp_path, None, escalar=0.25)
    _abre(tmp_path, 2)
    assert t._fraccion_de_esta_compra() == (0.25, 0)


def test_la_escalera_manda_sobre_el_escalar(tmp_path: Path) -> None:
    """Con las dos puestas gana la escalera, y hay que saberlo.

    En el C123 el escalar se deja a 0.33 como respaldo: si alguien vacia la
    escalera, se compra lo mas parecido a lo pedido en vez de un numero de
    otro dia.
    """
    t = _trader(tmp_path, ESCALERA_PEDIDA, escalar=0.25)
    assert t._fraccion_de_esta_compra()[0] == pytest.approx(0.33)


def test_los_peldanos_se_recortan_a_cero_uno() -> None:
    """Un 1.5 en el yaml no puede gastar el 150% del saldo libre.

    SE COMPRUEBA SOBRE EL CODIGO, no sobre un trader de mentira. La primera
    version de este test construia el trader con un helper que recortaba el
    mismo, asi que probaba el helper: al quitar el recorte de produccion,
    seguia verde. Lo destapo la ronda de mutacion.
    """
    bloque = FUENTE[FUENTE.index("self.fracciones_saldo = [") :][:260]
    assert "max(0.0, min(1.0, float(x)))" in bloque, (
        "sin recorte, un peldano mayor que 1 gastaria mas saldo del que hay"
    )
    assert "if x is not None" in bloque, "un null en la lista del yaml reventaria"

    # Y que el recorte haga lo que dice, con la misma expresion del codigo.
    recorta = lambda x: max(0.0, min(1.0, float(x)))  # noqa: E731
    assert [recorta(x) for x in (1.5, -0.2, 0.4)] == [1.0, 0.0, 0.4]


def test_el_escalar_tambien_se_recorta() -> None:
    """`fraccion_saldo` ya se recortaba; que siga haciendolo."""
    assert "max(0.0, min(1.0, float(fraccion_saldo or 0)))" in FUENTE


def test_cuenta_marcadores_en_disco_no_el_semaforo(tmp_path: Path) -> None:
    """La razon esta en el docstring del fichero: tras un reinicio el
    semaforo dice cero con posiciones vivas."""
    assert "_posiciones_abiertas()" in FUENTE
    fuente_metodo = FUENTE[FUENTE.index("def _fraccion_de_esta_compra") :][:2000]
    assert "self._posiciones_abiertas()" in fuente_metodo
    assert "_position_semaphore" not in fuente_metodo

    # y de verdad lee el disco
    t = _trader(tmp_path, ESCALERA_PEDIDA)
    _abre(tmp_path, 1)
    assert t._fraccion_de_esta_compra()[1] == 1


# ------------------------------------- que la rama libre se ejecute
def test_la_rama_libre_se_abre_tambien_con_solo_la_escalera() -> None:
    """LA TRAMPA 2 del docstring.

    La condicion era `fraccion_saldo > 0 and fraccion_base == "libre"`.
    Poniendo solo `fracciones_saldo` y el escalar en 0, el importe habria
    caido al `buy_amount` fijo y la escalera habria quedado muda.
    """
    assert "self.fraccion_saldo > 0 or self.fracciones_saldo" in FUENTE


def test_la_rama_libre_multiplica_por_el_peldano_no_por_el_escalar() -> None:
    assert "free / LAMPORTS_PER_SOL * _frac" in FUENTE
    assert "_frac, _peldano = self._fraccion_de_esta_compra()" in FUENTE


def test_el_log_dice_que_peldano_se_uso() -> None:
    """Sin esto, dos compras de distinto tamano son indistinguibles en el log
    y no hay forma de saber si la escalera se esta aplicando."""
    assert "peldano {_peldano + 1} de {len(self.fracciones_saldo)}" in FUENTE


def test_bot_runner_pasa_la_escalera() -> None:
    """Un parametro nuevo que nadie pasa es un parametro que no existe."""
    runner = (RAIZ / "src/bot_runner.py").read_text(encoding="utf-8")
    assert 'fracciones_saldo=cfg["trade"].get("fracciones_saldo")' in runner


# ------------------------------------------- la aritmetica del YAML
def test_las_cuentas_de_la_escalera_son_de_verdad() -> None:
    """La escalera 33/40/50 da 0,330 / 0,268 / 0,201 sobre 1 SOL libre.

    Se queda como prueba del MECANISMO aunque el C123 ya no la use: si
    manana se vuelve a configurar, estas son las cifras que tiene que dar.
    """
    libre = 1.0
    compras = []
    for frac in ESCALERA_PEDIDA:
        imp = libre * frac
        compras.append(imp)
        libre -= imp

    assert compras[0] == pytest.approx(0.330, abs=0.001)
    assert compras[1] == pytest.approx(0.268, abs=0.001)
    assert compras[2] == pytest.approx(0.201, abs=0.001)
    assert sum(compras) == pytest.approx(0.799, abs=0.001)
    assert libre == pytest.approx(0.201, abs=0.001)


def test_la_escalera_sube_de_verdad_el_porcentaje() -> None:
    """Si los peldanos no fueran crecientes, esto seria un escalar con pasos.

    Es la diferencia entre lo pedido (0,330 · 0,268 · 0,201) y lo que ya
    hacia el escalar al 33% (0,330 · 0,221 · 0,148).
    """
    assert ESCALERA_PEDIDA == sorted(ESCALERA_PEDIDA)
    assert len(set(ESCALERA_PEDIDA)) == len(ESCALERA_PEDIDA)

    libre, con_escalera = 1.0, []
    for frac in ESCALERA_PEDIDA:
        imp = libre * frac
        con_escalera.append(imp)
        libre -= imp
    libre, con_escalar = 1.0, []
    for _ in range(3):
        imp = libre * ESCALERA_PEDIDA[0]
        con_escalar.append(imp)
        libre -= imp

    assert con_escalera[1] > con_escalar[1]
    assert con_escalera[2] > con_escalar[2]


# ----------------------------------------------- lo que pide el C123
def test_el_c123_lleva_el_60_por_ciento_plano() -> None:
    """Lo ultimo pedido: "pon 60% sol balance por operaciones maximo 3
    simultaneamente". Tercer tamano del dia: 25% -> escalera 33/40/50 -> 60%.

    `fracciones_saldo` tiene que estar BORRADA, no puesta a 0 ni a
    [0.6, 0.6, 0.6]: dejarla escrita invita a creer que sigue mandando.
    """
    trade = yaml.safe_load(C123.read_text(encoding="utf-8"))["trade"]
    assert trade["fraccion_saldo"] == pytest.approx(0.60)
    assert "fracciones_saldo" not in trade, (
        "el C123 ya no usa escalera: la clave debe estar borrada"
    )
    assert trade["max_posiciones"] == PLAZAS_PEDIDAS
    assert trade["min_reserve_sol"] == RESERVA_PEDIDA
    assert str(trade["fraccion_base"]).strip().lower() == "libre"


def test_el_60_por_ciento_deja_las_posiciones_muy_desiguales() -> None:
    """Fija la consecuencia para que no sorprenda a nadie.

    Con base `libre` el 60% da 0,591 / 0,235 / 0,092 sobre 1 SOL: la primera
    es 6,4 VECES la tercera. Con la escalera 33/40/50 eran casi iguales
    (0,325 / 0,263 / 0,196). No es un fallo, es lo que se pidio -- pero esta
    escrito aqui y en el yaml para que sea una decision y no una sorpresa.
    """
    trade = yaml.safe_load(C123.read_text(encoding="utf-8"))["trade"]
    libre = 1.0 - trade["min_reserve_sol"]
    compras = []
    for _ in range(trade["max_posiciones"]):
        imp = libre * trade["fraccion_saldo"]
        compras.append(imp)
        libre -= imp
    assert compras[0] / compras[2] == pytest.approx(6.25, abs=0.3)
    assert sum(compras) / (1.0 - trade["min_reserve_sol"]) == pytest.approx(
        0.936, abs=0.01
    ), "el 60% despliega el 93% del saldo libre"


def test_el_suelo_de_compra_protege_la_orden_de_asegurar_el_capital() -> None:
    """El 60% hacia compras demasiado pequenas para devolver el capital.

    Es el choque entre dos ordenes de Manu del mismo dia: el 60% plano y
    "asegures el capital inicial". El suelo `compra_minima_sol` las hace
    compatibles. La cuenta exacta la comprueba
    `test_toda_compra_que_el_bot_haga_devuelve_el_capital` en
    tests/test_stop_asegura_ganancia.py.
    """
    trade = yaml.safe_load(C123.read_text(encoding="utf-8"))["trade"]
    assert trade["compra_minima_sol"] == pytest.approx(0.113)


def test_el_tope_de_compra_sigue_a_cero() -> None:
    """`tope_compra_sol` recortaria los peldanos en silencio.

    Ya paso el 15-oct con el 25%: habia un tope de 0,20 que lo anulaba en
    cuanto el saldo pasaba de 0,81 SOL. Con la escalera seria peor, porque el
    peldano grande es el que mas se recorta.
    """
    trade = yaml.safe_load(C123.read_text(encoding="utf-8"))["trade"]
    assert trade["tope_compra_sol"] == 0


def test_la_reserva_da_para_las_ventas_protegidas() -> None:
    """0,015 SOL es el colchon que NUNCA se gasta: paga las comisiones de
    prioridad de las VENTAS. Bajarlo de 0,03 libera SOL para comprar y deja
    menos margen para salir corriendo. La cuenta esta escrita en el yaml y
    aqui se comprueba que no miente.
    """
    cfg = yaml.safe_load(C123.read_text(encoding="utf-8"))
    fee = cfg["priority_fees"]["fixed_amount_venta_proteccion"]
    cu = cfg["compute_units"]["sell"]
    coste = fee * cu / 1e15
    assert coste == pytest.approx(0.0051, abs=0.0002)
    ventas = cfg["trade"]["min_reserve_sol"] / coste
    assert ventas >= 2.5, f"solo dan para {ventas:.1f} ventas protegidas"
    assert "0,0051 SOL" in C123.read_text(encoding="utf-8")


def test_el_modelo_de_los_tests_sabe_de_la_escalera() -> None:
    """`tests/modelo_tamano.py` existe para ser UNA copia fiel del calculo.

    Al anadir la escalera al codigo, el modelo se queda mintiendo si no se
    actualiza -- y su historia es exactamente esa: el 23-sep dos copias se
    quedaron VERDES midiendo el comportamiento viejo.
    """
    from modelo_tamano import fraccion_del_peldano

    t = {"fracciones_saldo": ESCALERA_PEDIDA, "fraccion_saldo": 0.25}
    assert fraccion_del_peldano(t, 0) == pytest.approx(0.33)
    assert fraccion_del_peldano(t, 1) == pytest.approx(0.40)
    assert fraccion_del_peldano(t, 2) == pytest.approx(0.50)
    assert fraccion_del_peldano(t, 9) == pytest.approx(0.50)
    assert fraccion_del_peldano({"fraccion_saldo": 0.25}, 1) == pytest.approx(0.25)
