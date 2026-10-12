"""El interruptor de avisos tiene que callar TAMBIEN al vigia.

PEDIDO EL 11-oct: "no envies mas notificaciones por telegram".

EL FALLO QUE TAPA. `avisos_telegram: false` lleva puesto desde hace tiempo y
el bot estaba callado de verdad: `bot_runner.py` llama a
`configurar_avisos(cfg["avisos_telegram"])` y eso pone `_SILENCIO` en el
notificador. Pero `deploy/avisar_caida.py` es OTRO proceso --lo lanza
`pump-bot-vigia`-- y no pasaba por esa llamada. Asi que seguia escribiendo
por Telegram cada vez que el bot se caia o se reiniciaba, con todo
"apagado". El 11-oct el bot se reinicio media docena de veces y el canal se
lleno.

ES LA FAMILIA DE SIEMPRE EN ESTE REPO: un interruptor que dice apagar algo y
solo lo apaga a medias, sin que nada lo diga. Igual que los frenos anti-rug
que vivian dentro de una fuente (FUCKETHAN, -91,9%).

LO QUE SE FIJA AQUI, y es lo delicado: que el silencio NO sea el valor por
defecto ante un problema. Si el yaml no se puede leer, o no hay ningun bot
encendido, el vigia TIENE que sonar: es el unico que cuenta que el bot esta
muerto, y callarse por un fichero ilegible es como se perdieron ocho dias en
septiembre.
"""

# ruff: noqa: S101, PLC0415

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "deploy"))


def _modulo():
    import avisar_caida

    return avisar_caida


def _escribe(carpeta: Path, nombre: str, *, encendido: bool, avisos) -> None:
    lineas = [f"name: {nombre}", f"enabled: {str(encendido).lower()}"]
    if avisos is not None:
        lineas.append(f"avisos_telegram: {str(avisos).lower()}")
    (carpeta / f"{nombre}.yaml").write_text("\n".join(lineas) + "\n", encoding="utf-8")


@pytest.fixture
def bots(tmp_path, monkeypatch):
    """Una carpeta `bots/` de mentira, para no tocar la de verdad."""
    carpeta = tmp_path / "bots"
    carpeta.mkdir()
    monkeypatch.setattr(_modulo(), "RAIZ", tmp_path)
    return carpeta


def test_con_avisos_apagados_el_vigia_se_calla(bots) -> None:
    """El caso pedido: el unico bot encendido dice false."""
    _escribe(bots, "curva", encendido=True, avisos=False)
    assert _modulo().avisos_apagados() is True


def test_con_avisos_encendidos_el_vigia_avisa(bots) -> None:
    _escribe(bots, "curva", encendido=True, avisos=True)
    assert _modulo().avisos_apagados() is False


def test_los_bots_apagados_no_cuentan(bots) -> None:
    """Un bot APAGADO con los avisos puestos no puede resucitar el ruido."""
    _escribe(bots, "curva", encendido=True, avisos=False)
    _escribe(bots, "unica", encendido=False, avisos=True)
    assert _modulo().avisos_apagados() is True


def test_basta_que_uno_encendido_los_quiera(bots) -> None:
    """Con dos bots encendidos y uno pidiendo avisos, el vigia suena.

    Es el lado seguro: perder un aviso de caida cuesta dias; recibir uno de
    mas cuesta una notificacion.
    """
    _escribe(bots, "curva", encendido=True, avisos=False)
    _escribe(bots, "otro", encendido=True, avisos=True)
    assert _modulo().avisos_apagados() is False


def test_sin_la_clave_se_avisa(bots) -> None:
    """`avisos_telegram` ausente no es "callate": es que nadie lo ha dicho."""
    _escribe(bots, "curva", encendido=True, avisos=None)
    assert _modulo().avisos_apagados() is False


def test_un_yaml_ilegible_no_puede_callar_al_vigia(bots) -> None:
    """EL TEST QUE IMPORTA. Si no se puede leer la configuracion, suena.

    Callarse ante un error seria exactamente el fallo que este script
    existe para evitar: el bot muerto y nadie enterandose.
    """
    (bots / "roto.yaml").write_text("esto: [no cierra\n", encoding="utf-8")
    assert _modulo().avisos_apagados() is False


def test_sin_bots_encendidos_se_avisa(bots) -> None:
    """Carpeta vacia o todo apagado: sigue sonando si el servicio cae."""
    assert _modulo().avisos_apagados() is False
    _escribe(bots, "curva", encendido=False, avisos=False)
    assert _modulo().avisos_apagados() is False


def test_el_silencio_se_escribe_en_el_diario() -> None:
    """Un silencio silencioso es como se pierden los avisos sin saberlo."""
    codigo = (RAIZ / "deploy/avisar_caida.py").read_text(encoding="utf-8")
    i = codigo.index("if avisos_apagados():")
    j = codigo.index("situacion = estado(SERVICIO)", i)
    bloque = codigo[i:j]
    assert "print(" in bloque and "stderr" in bloque, (
        "el vigia se calla sin dejar constancia en journalctl"
    )
    assert "NADIE te lo va a decir" in bloque, (
        "el aviso del diario no dice lo que de verdad se pierde al callarlo"
    )


def test_la_config_de_verdad_tiene_los_avisos_apagados() -> None:
    """Ata el interruptor al fichero que corre: hoy esta en false."""
    import yaml

    cfg = yaml.safe_load(
        (RAIZ / "bots/bot-paper-ai-entry.yaml").read_text(encoding="utf-8")
    )
    assert cfg.get("avisos_telegram") is False, (
        "si esto vuelve a true, el vigia vuelve a escribir por Telegram"
    )
