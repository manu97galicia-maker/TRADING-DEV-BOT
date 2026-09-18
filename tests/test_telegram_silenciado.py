"""El interruptor de Telegram: que `false` signifique de verdad "nada".

POR QUE HIZO FALTA UN VALOR NUEVO. `configurar_avisos` recibia una lista de
tipos y trataba la lista VACIA como "todos" (`set([])` es falsy, y el filtro se
salta cuando no hay tipos). Asi que no habia forma de decir "ninguno": poner
`[]` lo dejaba todo encendido, que es justo lo contrario de lo que parece.

Y habia una segunda puerta: `notify_ahora` se salta el filtro por tipo a
proposito --lo usa el aviso de "el bot se ha caido"--, asi que apagar por tipos
no la habria callado nunca.
"""

# ruff: noqa: S101, SLF001, FBT003

from pathlib import Path

import yaml

from monitoring import notifier

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _restaura() -> None:
    notifier.configurar_avisos(None)


def test_false_silencia_las_dos_puertas() -> None:
    """`notify` y `notify_ahora`. La segunda es la que se escapaba."""
    try:
        notifier.configurar_avisos(False)
        assert notifier._SILENCIO is True
        assert notifier.notify_ahora("no deberia salir") is False
        # `notify` no devuelve nada; se comprueba que ni siquiera mira el token.
        notifier.notify("no deberia salir", tipo="compra")
    finally:
        _restaura()


def test_la_lista_vacia_sigue_significando_todos() -> None:
    """Es contraintuitivo, y por eso se documenta con un test en vez de
    arreglarlo por sorpresa: hay yaml que dependen de ese comportamiento."""
    try:
        notifier.configurar_avisos([])
        assert notifier._SILENCIO is False
        assert notifier._TIPOS_PERMITIDOS == set()
    finally:
        _restaura()


def test_una_lista_de_tipos_sigue_filtrando() -> None:
    try:
        notifier.configurar_avisos(["compra"])
        assert notifier._SILENCIO is False
        assert notifier._TIPOS_PERMITIDOS == {"compra"}
    finally:
        _restaura()


def test_sin_la_clave_se_manda_todo() -> None:
    """Como funcionaba antes: los demas yaml no cambian de comportamiento."""
    notifier.configurar_avisos(None)
    assert notifier._SILENCIO is False
    assert notifier._TIPOS_PERMITIDOS == set()


def test_la_config_que_corre_lo_tiene_apagado() -> None:
    """Pedido el 19-sep. Si alguien lo enciende, este test lo dice."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert cfg["avisos_telegram"] is False


def test_los_demas_bots_no_se_quedan_mudos_sin_querer() -> None:
    """El silencio es de ESTE bot, no de todos."""
    raiz = CONFIG.parent
    otros = [
        p for p in raiz.glob("*.yaml")
        if p.name != CONFIG.name
        and yaml.safe_load(p.read_text(encoding="utf-8")).get("avisos_telegram")
        is False
    ]
    assert not otros, f"tambien silenciados: {[p.name for p in otros]}"
