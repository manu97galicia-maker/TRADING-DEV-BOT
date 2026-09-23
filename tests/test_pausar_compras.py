"""Modo estudio: ni una compra nueva, pero las abiertas siguen vigiladas.

LO QUE DISTINGUE ESTO DE PARAR EL BOT. Parar el servicio es lo primero que
se le ocurre a cualquiera para "dejar de comprar", y es la peor opcion
posible si hay posiciones abiertas: el dinero que YA esta dentro se queda sin
stop loss y sin escalera, que es exactamente el fallo que costo dinero en
esta cuenta. Con la bandera puesta el bot sigue vigilando y vendiendo lo que
tiene; lo unico que deja de hacer es abrir posiciones nuevas.

Y sigue MIDIENDO: cada compra que habria hecho queda apuntada con su importe
y su fuente. Ese registro es el producto del modo estudio -- sin el, un dia
en pausa no deja nada aprendido.
"""

# ruff: noqa: S101, PLR2004

import re
from pathlib import Path

import yaml

FUENTE = Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"
RUNNER = Path(__file__).resolve().parent.parent / "src/bot_runner.py"
CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _fuente() -> str:
    return FUENTE.read_text(encoding="utf-8")


def _cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _trade() -> dict:
    return _cfg()["trade"]


def _es_papel() -> bool:
    """Como lo lee el bot: `trade` manda y `filters` es el respaldo."""
    cfg = _cfg()
    return bool(
        cfg["trade"].get("paper_trade", cfg.get("filters", {}).get("paper_trade"))
    )


# ----------------------------------------- no se compra por ningun camino


def test_los_dos_caminos_al_dinero_estan_tapados() -> None:
    """Hay DOS sitios donde el bot firma una compra: el camino normal y el
    de los dips. Tapar solo uno dejaria el otro gastando."""
    texto = _fuente()
    guardias = texto.count("if self.pausar_compras:")
    assert guardias == 2, (
        f"hay {guardias} guardias de pausa y deberia haber 2 "
        "(camino normal y compra por dip)"
    )


def test_la_pausa_va_antes_de_firmar_en_el_camino_normal() -> None:
    """Un guardian DESPUES de la compra no es un guardian."""
    texto = _fuente()
    pausa = texto.index("if self.pausar_compras:")
    compra = texto.index("buy_result = await _active_buyer.execute(")
    assert pausa < compra


def test_la_pausa_va_antes_de_firmar_en_la_compra_por_dip() -> None:
    texto = _fuente()
    inicio = texto.index("async def _comprar_dip(")
    cuerpo = texto[inicio : inicio + 3000]
    pausa = cuerpo.index("if self.pausar_compras:")
    compra = cuerpo.index("await self.buyer.execute(")
    assert pausa < compra


# -------------------------------- las posiciones abiertas siguen vivas


def test_la_pausa_no_toca_la_vigilancia_de_lo_ya_comprado() -> None:
    """LA RAZON DE SER DE ESTA BANDERA. Si apareciera dentro del monitor de
    salida, una pausa dejaria sin stop loss al dinero que ya esta dentro --
    justo lo que se quiere evitar parando asi en vez de parando el bot.
    """
    texto = _fuente()
    inicio = texto.index("async def _monitor_tiered_exit(")
    fin = texto.index("# Marca para la proxima compra", inicio)
    assert "pausar_compras" not in texto[inicio:fin]


def test_la_pausa_no_impide_retomar_posiciones_al_arrancar() -> None:
    """Reiniciar en pausa no puede costar la vigilancia de lo abierto."""
    texto = _fuente()
    inicio = texto.index("async def _reanudar_una(")
    fin = texto.index("def _check_orphan_position", inicio)
    assert "pausar_compras" not in texto[inicio:fin]


def test_la_pausa_no_impide_vender() -> None:
    """Vender es justo lo que tiene que seguir funcionando."""
    texto = _fuente()
    inicio = texto.index("async def _sell_amount(")
    assert "pausar_compras" not in texto[inicio : inicio + 4000]


# ------------------------------------------- pero se sigue aprendiendo


def test_cada_compra_evitada_queda_apuntada() -> None:
    """Sin registro, un dia en pausa no deja nada aprendido."""
    texto = _fuente()
    assert "_apuntar_compra_en_pausa" in texto
    inicio = texto.index("def _apuntar_compra_en_pausa(")
    cuerpo = texto[inicio : inicio + 2000]
    assert "_log_trade" in cuerpo
    assert '"compra_en_pausa"' in cuerpo


def test_lo_apuntado_no_se_confunde_con_una_compra_de_verdad() -> None:
    """Si se apuntara como `buy`, los informes sumarian dinero que no se
    gasto y las cuentas del dia saldrian falsas."""
    texto = _fuente()
    inicio = texto.index("def _apuntar_compra_en_pausa(")
    cuerpo = texto[inicio : inicio + 2000]
    assert not re.search(r'_log_trade\(\s*"buy"', cuerpo)


def test_se_apunta_el_importe_que_se_habria_gastado() -> None:
    """Es el dato que permite luego contar cuanto habria movido cada fuente."""
    texto = _fuente()
    inicio = texto.index("def _apuntar_compra_en_pausa(")
    cuerpo = texto[inicio : inicio + 2000]
    assert "round(importe, 6)" in cuerpo


def test_un_fallo_apuntando_no_tumba_el_bot() -> None:
    texto = _fuente()
    inicio = texto.index("def _apuntar_compra_en_pausa(")
    cuerpo = texto[inicio : inicio + 2000]
    assert "except Exception" in cuerpo


# --------------------------------------------------------- cableado


def test_la_bandera_llega_desde_el_yaml() -> None:
    assert 'pausar_compras=cfg["trade"].get("pausar_compras", False)' in (
        RUNNER.read_text(encoding="utf-8")
    )


def test_por_defecto_se_compra() -> None:
    """Un bot sin esta clave en su YAML tiene que operar como siempre."""
    texto = _fuente()
    assert "pausar_compras: bool = False" in texto


def test_ahora_mismo_compra_de_verdad() -> None:
    """EL BOT ESTA GASTANDO DINERO. Este test es el recordatorio.

    El 18-sep se levanto la pausa a peticion de Manu, con 0,07 SOL por
    operacion. El 19-sep el tamano subio a 0,12, tambien pedido. El estudio
    dijo que las fuentes medidas pierden --entre -12% y -6% por operacion--
    y aun asi se arranca: es su decision.

    LO QUE CAMBIA AL SUBIR DE 0,07 A 0,12: cada operacion arriesga un 71%
    mas, y con el stop en -18% una perdida pasa de ~0,013 a ~0,022 SOL. Y
    caben menos posiciones a la vez con el mismo saldo, porque las cinco
    plazas y el tamano de compra tiran en sentidos contrarios.

    Si algun dia alguien vuelve a poner la pausa, este test se pone rojo y
    obliga a decirlo en voz alta en vez de que el bot se quede callado sin
    comprar durante dias sin que nadie lo note (que ya paso en agosto).
    """
    assert _trade()["pausar_compras"] is False
    # Y EL TAMANO: 30% del saldo libre (23-sep). Ha ido y venido mucho
    # (0,07 -> 0,12 -> 25% -> 0,04 -> 15% -> 30%), asi que se fija aqui para
    # que cualquier cambio sea deliberado.
    #
    # CON PORCENTAJE EL RIESGO ES PROPORCIONAL, que es la diferencia de fondo
    # con el importe fijo: si la cartera mengua, las compras menguan solas y no
    # hay que acordarse de bajarlas a mano.
    assert _trade()["fraccion_saldo"] == 0.30
    assert _trade()["buy_amount"] == 0, "o porcentaje o fijo, nunca los dos"
    # El tope duro se quito el 19-sep, pedido. Lo que sigue impidiendo que un
    # fallo vacie la cartera es el guardian del saldo: antes de firmar se lee el
    # saldo REAL y se exige que quepa la compra dejando intacta la reserva.
    assert _trade()["min_reserve_sol"] > 0, "sin reserva no quedaria gas para vender"


def test_en_pausa_se_sigue_operando_de_verdad_no_en_papel() -> None:
    """`paper_trade` simula TAMBIEN las ventas, asi que dejaria sin vender
    de verdad lo que ya esta comprado. La pausa no es eso."""
    assert _es_papel() is False
