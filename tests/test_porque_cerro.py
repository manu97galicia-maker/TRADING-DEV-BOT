"""El comando que contesta "por que se cerro esta moneda".

POR QUE HAY QUE TESTEARLO. Es una herramienta de DIAGNOSTICO, y una
herramienta de diagnostico que miente es peor que no tenerla: manda a cambiar
la configuracion en la direccion equivocada. Ya paso dos veces con los
contadores de `porque` y `bot`, que buscaban un texto del log que ya no se
escribia y daban "0 candidatas" pasara lo que pasara -- cero por no mirar y
cero por no haber se leen igual y llevan a sitios opuestos.

LO QUE SE COMPRUEBA: que encuentra la moneda por el mint, que saca el motivo
de la venta y no otro, que traduce los motivos que de verdad escribe el bot, y
que no se cae con un registro a medio escribir.
"""

# ruff: noqa: S101, PLC0415, ANN003, S106

import importlib.util
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GUION = RAIZ / "deploy" / "porque_cerro.py"
MINT = "D5Sd6RGFcsyCg4UgKMTax22ZYjBrivQXAAGAVAaQpump"


def _modulo(tmp_path: Path):  # noqa: ANN202
    """Carga el guion apuntando su registro a un fichero de prueba."""
    spec = importlib.util.spec_from_file_location("pc", GUION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.REGISTRO = tmp_path / "trades.log"
    return mod


def _registro(tmp_path: Path, filas: list[dict]) -> None:
    (tmp_path / "trades.log").write_text(
        "\n".join(json.dumps(f) for f in filas) + "\n", encoding="utf-8"
    )


def _compra(**kw) -> dict:
    base = {
        "timestamp": "2026-09-23T13:33:10",
        "action": "buy",
        "token_address": MINT,
        "symbol": "PRUEBA",
        "price": 0.0000002,
        "amount": 227000.0,
        "origen": "curva_96",
        "motivo": None,
        "pico": None,
    }
    return {**base, **kw}


def _venta(**kw) -> dict:
    base = {
        "timestamp": "2026-09-23T13:34:02",
        "action": "sell",
        "token_address": MINT,
        "symbol": "PRUEBA",
        "price": 0.00000021,
        "amount": 227000.0,
        "origen": "curva_96",
        "motivo": "anti_rug",
        "pico": 0.00000031,
    }
    return {**base, **kw}


# ------------------------------------------------- encontrar la moneda


def test_encuentra_la_moneda_por_el_mint_entero(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    mod = _modulo(tmp_path)
    _registro(tmp_path, [_compra(), _venta()])
    sys.argv = ["porque_cerro", MINT]
    mod.main()
    salida = capsys.readouterr().out
    assert "PRUEBA" in salida
    assert "curva_96" in salida, "tiene que decir que fuente la trajo"


def test_basta_con_los_primeros_caracteres(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """DESDE EL MOVIL NADIE TECLEA 44 CARACTERES. Un mint se copia mal a la
    primera y el comando contestaria "no hay nada" de una moneda que si esta,
    que es la peor forma de fallar: parece una respuesta."""
    mod = _modulo(tmp_path)
    _registro(tmp_path, [_compra(), _venta()])
    sys.argv = ["porque_cerro", "D5Sd6RGF"]
    mod.main()
    assert "PRUEBA" in capsys.readouterr().out


def test_no_mezcla_dos_monedas(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """El registro tiene TODAS las operaciones del bot. Si el filtro fallara,
    el motivo de otra moneda se leeria como el de esta."""
    mod = _modulo(tmp_path)
    otra = _compra(token_address="OTRA123", symbol="OTRA", motivo=None)
    otra_v = _venta(token_address="OTRA123", symbol="OTRA", motivo="stop_loss")
    _registro(tmp_path, [_compra(), otra, _venta(), otra_v])
    sys.argv = ["porque_cerro", MINT]
    mod.main()
    salida = capsys.readouterr().out
    assert "ANTI-RUG" in salida
    assert "OTRA" not in salida
    assert "STOP LOSS" not in salida


# ------------------------------------------------- el motivo, que es el dato


def test_dice_el_motivo_de_la_ultima_venta(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """Una posicion con escalera vende varias veces. El que cierra la posicion
    es el ULTIMO, no el primero -- y confundirlos diria "se cobro un take
    profit" de una que acabo en el stop."""
    mod = _modulo(tmp_path)
    _registro(tmp_path, [
        _compra(),
        _venta(motivo="tp_tier1_+30%", amount=45400.0),
        _venta(motivo="stop_loss", amount=181600.0),
    ])
    sys.argv = ["porque_cerro", MINT]
    mod.main()
    salida = capsys.readouterr().out
    por_que = salida.split("POR QUE SE CERRO:")[1]
    assert "STOP LOSS" in por_que
    # Y los dos siguen apareciendo en la lista de ventas, que es la historia.
    assert "tp_tier1" in salida


def test_traduce_todos_los_motivos_que_escribe_el_bot(tmp_path: Path) -> None:
    """QUE NO SE QUEDE NINGUNO SIN EXPLICAR. Un motivo suelto como
    `bajo_entrada` no le dice nada a quien lo lee a las 3 de la manana, y esa
    persona es justo el usuario de esta herramienta.

    La lista sale del propio codigo del bot, asi que si alguien anade una
    salida nueva y no la explica aqui, este test lo dice.
    """
    mod = _modulo(tmp_path)
    fuente = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    # Los motivos van escritos como cuarto argumento de `_sell_amount`.
    import re

    motivos = set(re.findall(r'_sell_amount\(\s*\n?\s*token_info,[^)]*?"([a-z_]+)"', fuente))
    motivos |= {"stop_loss", "trailing_stop", "anti_rug", "panic_sell",
                "bajo_entrada", "muerta"}
    sin_explicar = [m for m in motivos if mod.explica(m).startswith("Motivo '")]
    assert not sin_explicar, f"motivos sin explicacion en castellano: {sin_explicar}"


def test_los_peldanos_se_reconocen_aunque_lleven_numero(tmp_path: Path) -> None:
    """El motivo de un peldano lleva el numero y el objetivo dentro
    (`tp_tier2_+50%`), asi que no puede estar en una lista fija."""
    mod = _modulo(tmp_path)
    for m in ("tp_tier1_+30%", "tp_tier4_+150%", "mc_tier_$50000"):
        assert not mod.explica(m).startswith("Motivo '"), m


# ------------------------------------------------- que no se caiga


def test_una_posicion_abierta_no_se_cuenta_como_cerrada(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """Decir "se cerro por X" de una posicion viva mandaria a buscar un fallo
    inexistente; y al reves, callarse dejaria pensar que ya se vendio."""
    mod = _modulo(tmp_path)
    _registro(tmp_path, [_compra()])
    sys.argv = ["porque_cerro", MINT]
    mod.main()
    assert "TODAVIA NO SE HA VENDIDO" in capsys.readouterr().out


def test_una_linea_rota_no_tumba_el_diagnostico(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """El registro se escribe linea a linea mientras el bot opera: si se mira
    justo en mitad de una escritura, la ultima linea esta a medias. Eso no
    puede impedir leer las anteriores."""
    mod = _modulo(tmp_path)
    (tmp_path / "trades.log").write_text(
        json.dumps(_compra()) + "\n" + json.dumps(_venta()) + "\n{\"a\": ",
        encoding="utf-8",
    )
    sys.argv = ["porque_cerro", MINT]
    mod.main()
    assert "ANTI-RUG" in capsys.readouterr().out


def test_avisa_de_lo_que_se_dejo_encima_de_la_mesa(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """EL DATO QUE CONVIERTE ESTO EN UNA DECISION. Saber que se vendio a +5%
    no dice nada; saber que la moneda llego a +55% dice que la salida fue
    prematura y hay que tocar algo."""
    mod = _modulo(tmp_path)
    _registro(tmp_path, [_compra(), _venta()])
    sys.argv = ["porque_cerro", MINT]
    mod.main()
    salida = capsys.readouterr().out
    assert "llego a:" in salida
    assert "+55.0%" in salida
    assert "SE DEJO ENCIMA DE LA MESA" in salida
