"""Ya no se cierra ninguna posicion mirando el reloj: manda el precio.

PEDIDO EL 23-SEP: "elimina el hold time, que venda solo de acuerdo al stop".
Los tres relojes a cero. Historial de los dos primeros: 3 min -> 8 y 10 min ->
apagados.

POR QUE SE QUITARON. Un reloj no sabe nada de la moneda. "Ocho minutos sin
subir" es un hecho sobre el reloj, no sobre la moneda: la que tarda diez en
arrancar se cerraba en verde flojo o en rojo justo antes de hacer el
movimiento. Quien decide cuando se sale es el precio, y para eso esta el stop,
que ademas sube tras cada peldano.

EL FALLO QUE VIGILA ESTE FICHERO, y por eso existe. En esta configuracion el
cero significa APAGADO en todas partes. Si alguien escribiera la guarda de
otra forma --un `>=` en vez de un `>`, o comparando contra None-- el cero
pasaria a significar "YA", y el bot vendaria TODAS las posiciones en la
primera vuelta del vigilante, un segundo despues de comprarlas. Serian todas
las posiciones del dia cerradas al instante, con el bot funcionando y sin un
solo error en el log.

Cuando se hizo el cambio, 700 tests estaban en verde y NINGUNO cubria esto.
"""

# ruff: noqa: S101

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"
FUENTE = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


# --------------------------------------------------------- la decision


def test_los_tres_relojes_estan_apagados() -> None:
    """Los tres juntos, porque apagar dos y dejarse uno no es "sin relojes":
    la posicion se seguiria cerrando por tiempo y costaria otra tarde
    averiguar por cual de ellos."""
    t = _trade()
    assert t["venta_muerta_seg"] == 0
    assert t["venta_bajo_entrada_seg"] == 0
    assert t["max_hold_time"] == 0


def test_queda_algo_que_saque_de_una_posicion() -> None:
    """LO QUE NO PUEDE PASAR: quitar los relojes y quedarse sin ninguna salida.

    Sin stop y sin relojes, una moneda que se va a cero se queda en la wallet
    hasta que alguien mire. El stop es ahora la unica red por precio, asi que
    apagarlo tambien seria dejar las posiciones a pelo.
    """
    t = _trade()
    assert t["stop_loss_percentage"] > 0, (
        "sin relojes Y sin stop, nada cierra una posicion que se hunde"
    )
    assert t["take_profit_percentage"] > 0
    assert t["anti_rug_ratio"] > 0


def test_el_tope_de_vigilancia_no_es_un_reloj_de_venta() -> None:
    """`abandonar_tras_seg` sigue puesto con los demas apagados, y no es un
    descuido: no vende nada. Solo deja de vigilar una posicion que lleva 24 h
    sin poder valorarse, para que una moneda invendible no retenga su plaza
    para siempre.

    A CERO SERIA LO CONTRARIO de lo que parece: el bot abandonaria cada
    posicion nada mas abrirla.
    """
    assert _trade()["abandonar_tras_seg"] > 0


# ------------------------------------------- que el cero signifique APAGADO


def test_el_cero_apaga_la_venta_por_moneda_muerta() -> None:
    """Con `> 0` el cero apaga. Con `>=` vendaria todo en la primera vuelta."""
    assert "self.venta_muerta_seg > 0" in FUENTE, (
        "la guarda tiene que exigir MAYOR que cero, o el cero significaria "
        "'vender ya' en vez de 'apagado'"
    )


def test_el_cero_apaga_la_venta_por_debajo_de_la_entrada() -> None:
    """Aqui la guarda es el propio valor, que en Python es falso si es 0."""
    assert "if self.venta_bajo_entrada_seg and remaining > 1e-9:" in FUENTE


def test_el_cero_apaga_el_reloj_maximo() -> None:
    """`max_hold_time` a 0 ya estaba apagado, y hay una trampa apuntada en el
    yaml: estuvo atado a `abandonar_tras_seg`, y ponerlo a 0 hacia que el bot
    abandonase cada posicion a los 8 minutos. Tienen que seguir separados."""
    assert "and not self.max_hold_time" in FUENTE or "self.max_hold_time:" in FUENTE
    t = _trade()
    assert t["max_hold_time"] == 0
    assert t["abandonar_tras_seg"] != t["max_hold_time"], (
        "los dos relojes tienen que ser independientes"
    )
