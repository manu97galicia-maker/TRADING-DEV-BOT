"""7-nov: con el umbral a 0 (cualquier moneda) la fuente tiene que ir a ritmo."""

# ruff: noqa: S101, SLF001, ANN001, ANN003, ANN201, ANN202, PLR2004

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
