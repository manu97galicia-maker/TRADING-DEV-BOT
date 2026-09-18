"""Una sola linea que diga QUE configuracion esta corriendo de verdad.

EL PROBLEMA QUE RESUELVE, y costo tres dias. El droplet se quedo en el commit
bc942d8 del 16-sep y ahi siguio mientras se empujaban once commits, incluidos
los que quitaban la pausa, ponian 0,07 SOL y estrenaban la fuente de presion.
El bot no compraba y parecia roto: estaba haciendo exactamente lo que se le
habia pedido tres dias antes.

Saberlo pedia cuatro comandos y cuatro capturas desde el movil -- el commit,
si estaba en pausa, que fuentes y con cuanto compraba. Media manana para una
pregunta. Con este cartel, una captura de

    journalctl -u pump-bot | grep "CONFIG ACTIVA" | tail -1

las contesta todas de golpe.
"""

# ruff: noqa: S101, SLF001, ANN003, PLR2004

import types
from pathlib import Path

import yaml

from trading.universal_trader import UniversalTrader

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"


def _trader(**kw) -> UniversalTrader:
    """Un trader con lo justo para el cartel, sin tocar red ni wallet."""
    t = UniversalTrader.__new__(UniversalTrader)
    for attr, _corto in UniversalTrader.FUENTES:
        setattr(t, attr, {})
    t.pausar_compras = False
    t.paper_trade = False
    t.paper_engine = types.SimpleNamespace(enabled=False)
    t.tope_compra_sol = 0.07
    t.buy_amount = 0.07
    # Por defecto, importe fijo. Los tests que quieran el modo porcentaje lo
    # pasan por `kw`.
    t.fraccion_saldo = 0.0
    t.max_posiciones = 5
    for k, v in kw.items():
        setattr(t, k, v)
    return t


# ------------------------------------- el nombre corto NO es decorativo


def test_cada_fuente_tiene_su_metodo_de_arranque() -> None:
    """EL TEST QUE CAZA EL FALLO SILENCIOSO. El arranque busca
    `_run_<corto>_mode`; un nombre que no case deja la fuente APAGADA sin
    decir nada. Ya paso: el metodo es `_run_dexpaid_mode` y el nombre corto
    se escribio `dex_paid`, que no existe."""
    for attr, corto in UniversalTrader.FUENTES:
        assert hasattr(UniversalTrader, f"_run_{corto}_mode"), (
            f"la fuente '{attr}' se llama '{corto}' pero no hay "
            f"_run_{corto}_mode: se quedaria apagada en silencio"
        )


def test_la_lista_de_fuentes_esta_en_un_solo_sitio() -> None:
    """Vivia repetida en cuatro sitios y se desincronizo: al anadir `presion`
    falto en tres, y uno era el revisor del droplet, que habria dicho "sin
    fuentes activas" con el bot comprando."""
    texto = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    inicio = texto.index("acquisition_tasks: list[asyncio.Task] = []")
    bloque = texto[inicio : texto.index("if acquisition_tasks:", inicio)]
    assert "for attr, corto in self.FUENTES" in bloque
    # Ni un solo `if self.<fuente>.get("enabled")` escrito a mano.
    for attr, _corto in UniversalTrader.FUENTES:
        assert f'self.{attr}.get("enabled")' not in bloque


def test_todas_las_fuentes_del_yaml_estan_en_la_lista() -> None:
    """Una fuente configurada que no este en FUENTES nunca arrancaria."""
    f = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]
    conocidas = {attr for attr, _ in UniversalTrader.FUENTES}
    for clave, valor in f.items():
        if isinstance(valor, dict) and "enabled" in valor and valor["enabled"]:
            assert clave in conocidas or clave in {"dexscreener", "rugcheck"}, (
                f"'{clave}' esta encendida en el YAML y no esta en FUENTES"
            )


# --------------------------------------------------------- el cartel


def test_dice_si_compra_de_verdad() -> None:
    linea = _trader()._cartel_de_configuracion()
    assert "REALES" in linea
    assert "0.0700 SOL" in linea


def test_dice_si_esta_en_pausa() -> None:
    """Es la pregunta que mas veces se ha hecho a ciegas estos dias."""
    linea = _trader(pausar_compras=True)._cartel_de_configuracion()
    assert "EN PAUSA" in linea
    assert "REALES" not in linea


def test_distingue_el_papel_de_la_pausa() -> None:
    """No son lo mismo: en papel se simulan TAMBIEN las ventas."""
    linea = _trader(paper_trade=True)._cartel_de_configuracion()
    assert "PAPEL" in linea


def test_lista_las_fuentes_encendidas() -> None:
    t = _trader(presion={"enabled": True}, curva_96={"enabled": True})
    linea = t._cartel_de_configuracion()
    assert "presion" in linea
    assert "curva96" in linea
    assert "dexpaid" not in linea


def test_avisa_cuando_no_hay_ninguna_fuente() -> None:
    """Sin fuentes manda el listener normal, que compra otra cosa. Que no
    parezca que esta esperando cuando en realidad esta comprando recien
    nacidas."""
    assert "NINGUNA" in _trader()._cartel_de_configuracion()


def test_lleva_el_commit() -> None:
    """El dato que mas falta hizo: tres dias con codigo viejo sin poder verlo."""
    linea = _trader()._cartel_de_configuracion()
    assert "commit" in linea


def test_el_cartel_no_ensena_ningun_secreto() -> None:
    """Se pega en capturas de pantalla y va al log, que se comparte entero."""
    t = _trader(presion={"enabled": True})
    linea = t._cartel_de_configuracion()
    for prohibido in ("http", "api-key", "PRIVATE", "wss", "@", "="):
        assert prohibido not in linea, f"el cartel ensena '{prohibido}'"


def test_el_cartel_cabe_en_una_linea_de_movil() -> None:
    """Si no cabe, se corta en la captura justo donde esta lo importante."""
    t = _trader(presion={"enabled": True})
    assert len(t._cartel_de_configuracion()) < 140


def test_se_escribe_al_arrancar() -> None:
    texto = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    inicio = texto.index("async def start(self) -> None:")
    assert "self._cartel_de_configuracion()" in texto[inicio : inicio + 600]


# ------------------------------------------- el cartel tambien va a Telegram


def test_el_cartel_se_manda_al_movil_al_arrancar() -> None:
    """EL LOG NO LO LEE NADIE. Vive en un droplet que se administra desde un
    movil; Manu si lee Telegram. Esta es la linea que contesta "¿ya va en
    real?" sin escribir ni un comando -- la pregunta que se hizo cuatro veces
    en una semana porque no habia forma de verlo."""
    texto = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    inicio = texto.index("async def start(self) -> None:")
    trozo = texto[inicio : inicio + 1500]
    assert "BOT ARRANCADO" in trozo
    assert "notify(" in trozo


def test_el_aviso_de_arranque_no_puede_tumbar_el_bot() -> None:
    """Telegram mal configurado no puede impedir que el bot opere."""
    texto = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    inicio = texto.index("BOT ARRANCADO")
    assert "except Exception" in texto[inicio : inicio + 300]


def test_el_aviso_sale_una_sola_vez_por_arranque() -> None:
    """Un aviso repetido se vuelve ruido y deja de leerse. Este va en `start`,
    que corre una vez, no en ningun bucle."""
    texto = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    assert texto.count("BOT ARRANCADO") == 1


def test_el_cartel_dice_el_porcentaje_y_no_el_tope() -> None:
    """LA LINEA QUE SE MIRA AL ARRANCAR. Enseñaba `tope_compra_sol`, que desde
    que manda el porcentaje ya no es el importe sino un freno contra fallos:
    diria "REALES 0.5000 SOL" estando comprando 0,24.

    Mentir aqui es exactamente lo que no puede pasar: es la unica confirmacion
    de que la configuracion que corre es la que se cree.
    """
    t = _trader(fraccion_saldo=0.25, tope_compra_sol=0.5)
    cartel = t._cartel_de_configuracion()
    assert "25%" in cartel
    assert "saldo libre" in cartel
    assert "REALES 0.5000 SOL" not in cartel


def test_con_importe_fijo_el_cartel_sigue_dando_el_importe() -> None:
    """Si alguien apaga el porcentaje, el cartel vuelve a lo de antes."""
    t = _trader(fraccion_saldo=0.0, tope_compra_sol=0.07)
    assert "0.0700 SOL" in t._cartel_de_configuracion()
