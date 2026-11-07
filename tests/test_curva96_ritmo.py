"""7-nov: con el umbral a 0 (cualquier moneda) la fuente tiene que ir a ritmo."""

# ruff: noqa: S101, SLF001, ANN001, ANN002, ANN003, ANN201, ANN202, ARG001, ARG005, PLR2004, PLC0415

from monitoring import curva96_source as cs


def _fuente(**kw):
    return cs.Curva96Source("ws://x", None, None, umbral=0.0, **kw)


def test_cada_moneda_como_mucho_cada_2_segundos(monkeypatch):
    reloj = [100.0]
    monkeypatch.setattr(cs, "monotonic", lambda: reloj[0])
    f = _fuente(revisar_cada_seg=2.0)
    assert f._toca_esperar("A") is False
    reloj[0] += 1.0
    assert f._toca_esperar("A") is True
    assert f._toca_esperar("B") is False  # otra moneda no espera
    reloj[0] += 1.5
    assert f._toca_esperar("A") is False


def test_sin_ritmo_es_como_antes():
    f = _fuente()
    assert f._toca_esperar("A") is False and f._toca_esperar("A") is False


def test_presion_insuficiente_se_cuenta_siempre_y_se_escribe_cada_30s(monkeypatch):
    reloj = [100.0]
    monkeypatch.setattr(cs, "monotonic", lambda: reloj[0])
    monkeypatch.setattr(cs.volumen, "metricas_segunda_ola", lambda *_a, **_k: {})
    lineas = []
    monkeypatch.setattr(cs.logger, "info", lambda *a, **_k: lineas.append(a))
    f = _fuente(log_presion_cada_seg=30.0)
    for _ in range(5):
        assert f._presion_suficiente("A", "A") is False
        reloj[0] += 2.0
    assert f._embudo["sin_presion"] == 5 and len(lineas) == 1
    reloj[0] += 30.0
    f._presion_suficiente("A", "A")
    assert len(lineas) == 2


def test_las_dos_ultimas_tienen_que_ser_compras(monkeypatch):
    """7-nov "dos ultimas tx sean compras": con presion OK y una venta en las
    dos ultimas, no se manda a comprar y la moneda queda para volver a mirar."""
    import asyncio

    f = _fuente(ultimas_compras=2)
    monkeypatch.setattr(cs.volumen, "progreso_curva", lambda _m: (0.5, 40.0))
    monkeypatch.setattr(f, "_sin_mecha_vertical", lambda *_a: True)
    monkeypatch.setattr(f, "_presion_suficiente", lambda *_a: True)
    llamadas = []

    async def _hasta_aqui(*_a, **_k):
        llamadas.append(1)

    monkeypatch.setattr(cs.volumen, "vio_la_creacion", lambda _m: True)
    monkeypatch.setattr(f, "_fuera_de_edad", lambda *_a: False)
    monkeypatch.setattr(f, "_es_bundle", lambda *_a, **_k: False)
    monkeypatch.setattr(cs, "build_pumpfun_token_info", _hasta_aqui)

    monkeypatch.setattr(cs.volumen, "ultimas_son_compra", lambda _m, n: False)
    f._probadas.add("M")
    asyncio.run(f._evaluar("M", _hasta_aqui))
    assert llamadas == [] and "M" not in f._probadas
    assert f._embudo["ultimas_no_compras"] == 1

    monkeypatch.setattr(cs.volumen, "ultimas_son_compra", lambda _m, n: n == 2)
    asyncio.run(f._evaluar("M", _hasta_aqui))
    assert llamadas == [1]  # pasa y va a construir la moneda


def test_la_estrategia_pide_las_dos_ultimas_compras():
    from pathlib import Path

    import yaml

    cv = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "bots/bot-real-presion.yaml").read_text()
    )["filters"]["curva_96"]
    assert cv["ultimas_compras"] == 3  # despues: "las 3 ultimas"


def test_minimo_de_market_cap_5600(monkeypatch):
    """7-nov "minimo compras monedas de 5.6k de market cap": por debajo no se
    manda a comprar y la moneda queda para volver a mirar."""
    f = _fuente(min_mc_usd=5600, sol_usd=lambda: 90.0)
    # MC = precio x 1.000.000.000 x 90 $
    monkeypatch.setattr(cs.volumen, "precio_en_vivo", lambda _m, _e: 5000 / 90 / 1e9)
    assert f._llega_al_mc("M") is False and f._embudo["bajo_mc_minimo"] == 1
    monkeypatch.setattr(cs.volumen, "precio_en_vivo", lambda _m, _e: 5700 / 90 / 1e9)
    assert f._llega_al_mc("M") is True
    monkeypatch.setattr(cs.volumen, "precio_en_vivo", lambda _m, _e: None)
    assert f._llega_al_mc("M") is False  # sin precio no se afirma nada


def test_el_bot_le_pasa_el_precio_de_sol_vivo(tmp_path, monkeypatch):
    """La fuente lee el SOL del trader en cada llamada (se actualiza en vivo)."""
    from test_tp_sl_en_vivo import _construir

    t, ut = _construir("bots/bot-real-presion.yaml", tmp_path, monkeypatch)
    capturado = {}
    original = cs.Curva96Source.__init__

    def _init(self, *a, **k):
        original(self, *a, **k)
        capturado["f"] = self

    async def _run(self, _cb):
        return None

    monkeypatch.setattr(cs.Curva96Source, "__init__", _init)
    monkeypatch.setattr(cs.Curva96Source, "run", _run)
    import asyncio

    asyncio.run(t._run_curva96_mode())
    f = capturado["f"]
    assert f.min_mc_usd == 5600 and f.ultimas_compras == 3
    monkeypatch.setattr(ut, "_SOL_USD", 123.0)
    assert f._sol_usd() == 123.0


def test_menos_de_10_minutos_no_y_se_vuelve_a_mirar(monkeypatch):
    """7-nov "no compres monedas con menos de 10 minutos de vida"."""
    import asyncio

    f = _fuente(min_edad_seg=600)
    monkeypatch.setattr(cs.volumen, "progreso_curva", lambda _m: (0.5, 40.0))
    monkeypatch.setattr(cs.volumen, "edad_real", lambda _m: 300.0)
    llamadas = []

    async def _cb(*_a):
        llamadas.append(1)

    monkeypatch.setattr(
        f, "_presion_suficiente", lambda *_a: llamadas.append("presion")
    )
    f._probadas.add("M")
    asyncio.run(f._evaluar("M", _cb))
    assert llamadas == [] and "M" not in f._probadas  # ni mira la presion
    assert f._embudo["demasiado_nueva"] == 1
    monkeypatch.setattr(cs.volumen, "edad_real", lambda _m: 601.0)
    assert f._demasiado_joven("M") is False


def test_la_estrategia_pide_10_minutos():
    from pathlib import Path

    import yaml

    fl = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "bots/bot-real-presion.yaml").read_text()
    )["filters"]
    assert fl["curva_96"]["min_edad_seg"] == 600 and fl["curva_96"]["max_edad_seg"] == 0
    assert fl["dex_presion"]["min_edad_min"] == 10
