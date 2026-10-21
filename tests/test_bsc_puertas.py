"""Las TRES PUERTAS que separan papel de dinero real en el bot BSC.

`INSTRUCCIONES.md`: "Hay tres puertas independientes: los dos booleanos del
YAML, la frase exacta del entorno y una clave BSC valida. Si cualquiera
falla, no se firma."

Este fichero existe porque esa frase tiene que ser VERDAD, no una intencion.
Cada test de aqui tumba una forma distinta de abrir real sin querer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bsc.config import (  # noqa: E402
    FRASE_LIVE,
    ConfigBSC,
    ConfigInvalidaError,
    Estrategia,
    cargar,
    puertas_abiertas,
)

CLAVE_VALIDA = "0x" + "a" * 64


def _cfg(*, paper: bool, live: bool) -> ConfigBSC:
    return ConfigBSC(Path("x.yaml"), paper, live, Estrategia())


def _entorno(frase: str = FRASE_LIVE, clave: str = CLAVE_VALIDA) -> dict[str, str]:
    return {"BSC_ENABLE_LIVE_TRADING": frase, "BSC_PRIVATE_KEY": clave}


def test_con_las_tres_puertas_se_firma() -> None:
    """El caso bueno. Si este falla, el bot no podria operar nunca."""
    ok, faltan = puertas_abiertas(_cfg(paper=False, live=True), _entorno())
    assert ok and faltan == []


@pytest.mark.parametrize(
    ("paper", "live", "que_falla"),
    [
        (True, True, "paper_trade sigue en true"),
        (False, False, "live_transactions sigue en false"),
        (True, False, "las dos banderas estan en papel"),
    ],
)
def test_las_dos_banderas_del_yaml_hacen_falta(
    paper: bool, live: bool, que_falla: str
) -> None:
    """`paper_trade: false` por si solo NO basta, y es deliberado.

    Es la bandera que mas facil se queda a medias al copiar la plantilla:
    alguien pone `paper_trade: false` creyendo que ya esta y se va. Exigir
    ademas `live_transactions: true` obliga a un segundo acto consciente.
    """
    ok, faltan = puertas_abiertas(_cfg(paper=paper, live=live), _entorno())
    assert not ok, f"se firmaria con {que_falla}"
    assert any("YAML" in f for f in faltan)


@pytest.mark.parametrize(
    "frase",
    [
        "",
        "i_understand_bsc_live_trading",       # minusculas
        " I_UNDERSTAND_BSC_LIVE_TRADING ",      # con espacios
        "I_UNDERSTAND_BSC_LIVE_TRADING_YES",    # con cola
        "yes",
        "true",
        "1",
    ],
)
def test_la_frase_se_compara_entera_y_sin_normalizar(frase: str) -> None:
    """Si valiera "yes" o la frase en minusculas, dejaria de ser una puerta.

    No se hace `.strip()` ni `.lower()` a proposito: el valor de esta
    variable tiene que haberse escrito a conciencia, letra por letra.
    """
    ok, faltan = puertas_abiertas(_cfg(paper=False, live=True), _entorno(frase=frase))
    assert not ok
    assert any("BSC_ENABLE_LIVE_TRADING" in f for f in faltan)


@pytest.mark.parametrize(
    ("clave", "por_que"),
    [
        ("", "vacia"),
        ("0x" + "a" * 63, "un caracter de menos"),
        ("0x" + "a" * 65, "un caracter de mas"),
        ("0x" + "z" * 64, "no es hexadecimal"),
        ("abandon abandon abandon abandon abandon abandon", "es una seed phrase"),
        # Y una del largo exacto de una clave, por si alguien creyera que lo
        # unico que separa una seed phrase de una clave es la longitud.
        (" ".join(["abcdef"] * 9) + "aaaaaaaaa", "seed phrase de 64 caracteres"),
    ],
)
def test_una_clave_con_mala_forma_no_abre_la_puerta(clave: str, por_que: str) -> None:
    """La seed phrase es el error caro, y `INSTRUCCIONES.md` lo prohibe.

    Solo se mira la FORMA: 32 bytes en hexadecimal, con o sin `0x`. No se
    deriva la direccion ni se toca la clave para nada mas.
    """
    ok, faltan = puertas_abiertas(_cfg(paper=False, live=True), _entorno(clave=clave))
    assert not ok, f"se firmaria con una clave {por_que}"
    assert any("BSC_PRIVATE_KEY" in f for f in faltan)


def test_lo_que_falta_nunca_lleva_el_valor_del_secreto() -> None:
    """ "No imprimir, registrar ni versionar claves privadas."

    Los mensajes de esta funcion acaban en logs y en pantallazos de Telegram.
    Dicen QUE falta, nunca CUANTO vale.
    """
    secreto = "0x" + "b" * 64
    frase_inventada = "ESTA_NO_ES_LA_FRASE_SECRETA_DE_MANU"
    _, faltan = puertas_abiertas(
        _cfg(paper=False, live=True), _entorno(frase=frase_inventada, clave=secreto)
    )
    todo = " ".join(faltan)
    assert secreto not in todo
    assert "b" * 16 not in todo
    assert frase_inventada not in todo


def test_un_yaml_sin_bloque_mode_se_queda_en_papel(tmp_path: Path) -> None:
    """La AUSENCIA de una bandera nunca puede significar "si".

    Un fichero a medio escribir, o uno al que se le borro el bloque sin
    querer, tiene que caer del lado seguro.
    """
    ruta = tmp_path / "medio.yaml"
    ruta.write_text(yaml.safe_dump({"strategy": {"compra_usd": 25}}), encoding="utf-8")
    cfg = cargar(ruta)
    assert cfg.paper_trade is True
    assert cfg.live_transactions is False
    assert cfg.es_real is False


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("compra_usd", 0),
        ("compra_usd", -25),
        ("stop_loss", 0),
        ("stop_loss", 1),
        ("stop_loss", 1.5),
        ("max_impuesto_total", 1.0),
        ("max_posiciones", 0),
        ("max_entry_mc_usd", 0),
        ("reserva_bnb_usd", -1),
    ],
)
def test_los_numeros_imposibles_se_paran_antes_de_la_cadena(
    campo: str, valor: float, tmp_path: Path
) -> None:
    """Un `stop_loss: 1` seria vender a precio cero. Mejor no arrancar."""
    ruta = tmp_path / "malo.yaml"
    ruta.write_text(
        yaml.safe_dump({"mode": {"paper_trade": True}, "strategy": {campo: valor}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigInvalidaError):
        cargar(ruta)
