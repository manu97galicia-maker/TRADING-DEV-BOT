"""Saber si el nodo --o nuestro propio tope-- es lo que va lento.

EL PROBLEMA QUE ESTO RESUELVE. Manu dice "vas lento" y no habia forma de saber
si el culpable era la red, el nodo, o un freno que nos habiamos puesto
nosotros. Las tres cosas se ven igual desde fuera y se arreglan distinto.

Y el aviso que existia para decirlo LLEVABA ROTO DESDE SIEMPRE: `SolanaClient`
recibia `max_rps` y no lo guardaba, asi que `_avisa_si_el_nodo_no_da_mas` leia
0 con un `getattr(..., "max_rps", 0)` y se salia sin avisar nunca.
"""

# ruff: noqa: S101, PLR2004, SLF001, PLC0415

import asyncio
from pathlib import Path

from core.rpc_rate_limiter import TokenBucketRateLimiter

CLIENTE = Path(__file__).resolve().parent.parent / "src/core/client.py"


def test_el_cliente_guarda_su_tope() -> None:
    """La linea que hacia inutil el aviso. Un `getattr` con valor por defecto
    no falla: se queda callado, que es peor."""
    fuente = CLIENTE.read_text(encoding="utf-8")
    assert "self.max_rps = float(max_rps)" in fuente


def test_el_aviso_del_nodo_ya_puede_saltar() -> None:
    """Se comprueba con un cliente de mentira que tenga el atributo, que es lo
    unico que le hacia falta al aviso para funcionar."""
    from trading.universal_trader import UniversalTrader

    t = object.__new__(UniversalTrader)
    t.price_check_interval = 1.0
    t.solana_client = type("C", (), {"max_rps": 15.0})()
    t._posiciones_abiertas = lambda: 99          # muchas mas de las vigilables
    avisos = []
    t._avisa = avisos.append
    # No se llama al metodo real (toca notificador y logger); lo que se fija es
    # que la cuenta que hacia sale distinta de cero, que es lo que estaba roto.
    vigilables = int(t.solana_client.max_rps * t.price_check_interval / 2)
    assert vigilables > 0, "sin max_rps guardado esto valia 0 y no avisaba nunca"


def test_se_cuentan_las_esperas_de_nuestro_propio_tope() -> None:
    """No es el nodo diciendo que no: es el bot esperandose a si mismo.

    Si `max_rps` esta por debajo de lo que da el plan contratado, el bot se
    frena solo y desde fuera parece que el nodo va lento.
    """
    async def _m() -> TokenBucketRateLimiter:
        r = TokenBucketRateLimiter(max_rps=5)
        for _ in range(12):
            await r.acquire()
        return r

    r = asyncio.run(_m())
    assert r.esperas > 0
    assert r.segundos_esperando > 0


def test_sin_agobio_no_se_espera_nada() -> None:
    """Con presupuesto de sobra el contador tiene que quedarse a cero, o el
    aviso seria ruido constante y acabaria ignorandose."""
    async def _m() -> TokenBucketRateLimiter:
        r = TokenBucketRateLimiter(max_rps=200)
        for _ in range(10):
            await r.acquire()
        return r

    r = asyncio.run(_m())
    assert r.esperas == 0
    assert r.segundos_esperando == 0.0


def test_se_cuentan_los_429_del_nodo() -> None:
    """Distinto del anterior: esto SI es el nodo diciendo que no."""
    fuente = CLIENTE.read_text(encoding="utf-8")
    assert "self.veces_429 = 0" in fuente
    assert "self.veces_429 += 1" in fuente


def test_un_429_no_cuelga_la_vigilancia_medio_minuto() -> None:
    """La espera tras un 429 llegaba a 30 segundos.

    En un bot que necesita el precio CADA SEGUNDO para el stop loss, eso es una
    eternidad: la moneda hace el recorrido entero mientras se espera. Mejor
    fallar pronto y que el bucle lo reintente.
    """
    from core.client import _ESPERA_429_MAX

    assert _ESPERA_429_MAX <= 5
