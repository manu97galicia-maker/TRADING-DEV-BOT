"""El bot dice POR QUE no compra, sin que nadie tenga que leer un log.

EL PROBLEMA QUE RESUELVE. El bot llevaba dias encendido, en modo real, sin
comprar una sola moneda. El motivo de cada descarte SI se escribia, pero en un
log que vive en un droplet que se administra desde un movil: mezclado entre
miles de lineas, en la practica no lo lee nadie. Cada hipotesis costaba un
viaje de ida y vuelta, y se probaron varias equivocadas antes de dar con la
buena.

Manu si lee Telegram. Asi que el bot lo cuenta por ahi solo: un mensaje cada
cinco minutos con los motivos agrupados, y SOLO cuando no ha comprado nada.
"""

# ruff: noqa: S101, SLF001, ANN001, PLR2004, PLC0415, ARG005

import asyncio
from pathlib import Path
from types import SimpleNamespace

import yaml

from trading.universal_trader import UniversalTrader

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"


def _trader(seg: float = 300.0) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    t._descartes = {}
    t._compras_del_resumen = 0
    t._ultimo_resumen = 0.0
    t.resumen_descartes_seg = seg
    return t


def _moneda(sym: str = "TEST") -> SimpleNamespace:
    return SimpleNamespace(symbol=sym, mint="m1", additional_data={})


# --------------------------------------------------- contar los descartes


def test_descartar_cuenta_y_no_solo_loguea() -> None:
    """Las dos cosas. Loguear sin contar deja el problema donde estaba."""
    t = _trader()
    t._descarta(_moneda(), "un titular concentra demasiado")
    t._descarta(_moneda("OTRA"), "un titular concentra demasiado")
    assert t._descartes == {"un titular concentra demasiado": 2}


def test_el_motivo_agrupa_monedas_distintas() -> None:
    """Si el motivo llevara el simbolo o los numeros de cada moneda, cada
    descarte seria unico, el recuento saldria todo a uno y no diria nada --
    que es justo lo que pasaba leyendo el log a mano."""
    t = _trader()
    for sym in ("PEPE", "DOGE", "SHIB"):
        t._descarta(_moneda(sym), "dev bloqueado por 1 rug(s)")
    assert list(t._descartes.values()) == [3]


# ------------------------------------------------------------- el parte


def test_sin_descartes_no_hay_parte() -> None:
    assert _trader()._resumen_de_descartes() is None


def test_el_motivo_mas_frecuente_va_primero() -> None:
    """El mensaje se corta en la pantalla del movil: el motivo gordo tiene que
    estar arriba o no se ve."""
    t = _trader()
    for _ in range(9):
        t._descarta(_moneda(), "un titular concentra demasiado")
    t._descarta(_moneda(), "dev bloqueado")
    parte = t._resumen_de_descartes()
    assert parte.index("titular") < parte.index("dev bloqueado")
    assert "<b>9</b>" in parte


def test_el_parte_dice_cuantas_se_compraron() -> None:
    """Sin ese numero no se sabe si 40 descartes son un problema o el filtro
    haciendo su trabajo mientras compra otras."""
    t = _trader()
    t._descarta(_moneda(), "motivo")
    t._cuenta_compra_para_el_resumen()
    assert "1 compradas" in t._resumen_de_descartes()


def test_el_parte_no_ensena_ningun_secreto() -> None:
    """Va a Telegram y se pega en capturas."""
    t = _trader()
    t._descarta(_moneda(), "un titular concentra más del 15%")
    parte = t._resumen_de_descartes()
    for prohibido in ("http", "api-key", "PRIVATE", "wss"):
        assert prohibido not in parte


# ------------------------------------------- cuando SI y cuando NO se manda


def _con_notify(monkeypatch) -> list[str]:
    enviados: list[str] = []
    import monitoring.notifier as n

    monkeypatch.setattr(n, "notify", lambda msg, *a, **k: enviados.append(msg))
    return enviados


def test_si_esta_comprando_no_se_avisa_de_nada(monkeypatch) -> None:
    """Descartar monedas es lo que hace un filtro. Solo es un problema cuando
    NINGUNA pasa: un aviso que sale siempre deja de leerse a la segunda semana,
    y entonces no sirve el dia que de verdad pasa algo."""
    t = _trader(seg=0.001)
    enviados = _con_notify(monkeypatch)
    t._descarta(_moneda(), "motivo")
    t._cuenta_compra_para_el_resumen()
    t._avisar_de_los_descartes_si_toca()
    assert enviados == []


def test_si_no_compra_nada_si_se_avisa(monkeypatch) -> None:
    t = _trader(seg=0.001)
    enviados = _con_notify(monkeypatch)
    t._descarta(_moneda(), "un titular concentra demasiado")
    t._avisar_de_los_descartes_si_toca()
    assert len(enviados) == 1
    assert "titular" in enviados[0]


def test_el_contador_se_vacia_tras_avisar(monkeypatch) -> None:
    """Si no, el siguiente parte arrastraria los descartes del anterior y los
    numeros no dirian nada de los ultimos cinco minutos."""
    t = _trader(seg=0.001)
    _con_notify(monkeypatch)
    t._descarta(_moneda(), "motivo")
    t._avisar_de_los_descartes_si_toca()
    assert t._descartes == {}
    assert t._compras_del_resumen == 0


def test_no_se_avisa_antes_de_tiempo(monkeypatch) -> None:
    t = _trader(seg=99999)
    enviados = _con_notify(monkeypatch)
    t._descarta(_moneda(), "motivo")
    t._avisar_de_los_descartes_si_toca()
    assert enviados == []


def test_a_cero_el_parte_esta_apagado(monkeypatch) -> None:
    t = _trader(seg=0)
    enviados = _con_notify(monkeypatch)
    t._descarta(_moneda(), "motivo")
    t._avisar_de_los_descartes_si_toca()
    assert enviados == []


# ------------------------------------------- el vigia corre SIEMPRE


def test_el_vigia_no_cuelga_del_bucle_de_posiciones() -> None:
    """EL ERROR QUE COMETI Y CORREGI. La primera version colgaba el aviso del
    bucle que vigila las posiciones abiertas. Ese bucle SOLO corre cuando hay
    algo comprado, asi que el aviso de "no compra nada" no habria salido jamas
    justo en el unico caso para el que existe.
    """
    texto = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    inicio = texto.index("async def _monitor_tiered_exit(")
    fin = texto.index("# Marca para la proxima compra", inicio)
    assert "_avisar_de_los_descartes_si_toca" not in texto[inicio:fin]
    # Y si esta en su propia tarea, lanzada con las fuentes.
    assert "asyncio.create_task(self._vigilar_los_descartes())" in texto


def test_el_vigia_sobrevive_a_un_fallo_suyo() -> None:
    """Un aviso que revienta no puede llevarse por delante al bot."""
    t = _trader(seg=0.001)
    t._resumen_de_descartes = lambda: 1 / 0  # type: ignore[assignment]
    t._descarta(_moneda(), "motivo")

    async def _una_vuelta() -> None:
        tarea = asyncio.create_task(t._vigilar_los_descartes())
        await asyncio.sleep(0.05)
        tarea.cancel()

    asyncio.run(_una_vuelta())  # no debe propagar


# ----------------------------------------- el tope de titular de la fuente


def test_la_fuente_de_presion_trae_su_propio_tope_de_titular() -> None:
    """EL FILTRO QUE TENIA EL BOT SIN COMPRAR. El global (0,15) esta pensado
    para monedas recien nacidas; esta fuente compra a media vida, donde un
    titular con mas del 15% es lo normal, y las descartaba todas."""
    texto = (RAIZ / "src/monitoring/presion_source.py").read_text(encoding="utf-8")
    inicio = texto.index('"acquisition_source": "presion"')
    bloque = texto[inicio : inicio + 900]
    assert '"max_holder_pct"' in bloque


def test_a_ciegas_el_tope_de_titular_va_mas_apretado() -> None:
    """Sin haber visto nacer la moneda no hay nada mas que mire, asi que este
    tiene que pedir mas -- igual que el de bundle."""
    p = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]["presion"]
    assert p["max_holder_pct_sin_historia"] < p["max_holder_pct"]


def test_el_tope_de_la_fuente_es_mas_flojo_que_el_global() -> None:
    """Si no lo fuera, no habria servido de nada: el global seguiria mandando."""
    f = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]
    assert f["presion"]["max_holder_pct"] > f["max_holder_pct"]


def test_el_global_no_se_ha_tocado() -> None:
    """Aflojar para esta fuente no puede aflojar para las demas: el 0,15 tiene
    sentido en una moneda recien nacida."""
    f = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]
    assert f["max_holder_pct"] == 0.15
