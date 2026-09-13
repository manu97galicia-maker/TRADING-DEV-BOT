"""Tests del ritmo de peticiones a DexScreener.

El fallo real, visto en produccion el 12-sep: el log se llenaba de
"DexScreener rate limited (429)" en TODOS los endpoints, vuelta tras
vuelta, y el bot no veia ni una candidata. Dos causas que se sumaban:

1. Se creaba un cliente NUEVO en cada sondeo, y con el un limitador nuevo
   con el cubo lleno. El limitador no limitaba nada ENTRE vueltas.
2. Un 429 no costaba nada: se devolvia None y la vuelta siguiente machacaba
   igual, asi que el bloqueo no se levantaba nunca.
"""

# ruff: noqa: S101, SLF001, PLR2004, PLC0415

import asyncio

from dexscreener.client import DexScreenerClient


def test_un_429_impone_un_castigo_minimo() -> None:
    cliente = DexScreenerClient()
    assert cliente._cooldown_seconds == 0.0
    cliente._penalizar(None)
    assert cliente._cooldown_seconds >= 5.0


def test_el_castigo_se_dobla_con_cada_429_seguido() -> None:
    """Insistir al mismo ritmo es lo que mantenia el bloqueo vivo."""
    cliente = DexScreenerClient()
    cliente._penalizar(None)
    primero = cliente._cooldown_seconds
    cliente._penalizar(None)
    assert cliente._cooldown_seconds > primero


def test_el_castigo_tiene_techo() -> None:
    """Doblar sin limite dejaria el bot mudo horas por un mal minuto."""
    cliente = DexScreenerClient()
    for _ in range(20):
        cliente._penalizar(None)
    assert cliente._cooldown_seconds <= 60.0


def test_se_respeta_retry_after_si_la_api_lo_dice() -> None:
    """Si el servidor dice cuanto esperar, manda el servidor."""
    cliente = DexScreenerClient()
    cliente._penalizar("12")
    assert cliente._cooldown_seconds == 12.0


def test_un_retry_after_ilegible_no_rompe_nada() -> None:
    cliente = DexScreenerClient()
    cliente._penalizar("en un ratito")
    assert cliente._cooldown_seconds >= 5.0


def test_esperar_no_bloquea_si_no_hay_castigo() -> None:
    cliente = DexScreenerClient()
    asyncio.run(cliente._esperar_enfriamiento())


def test_el_limitador_deja_margen_bajo_el_tope_de_la_api() -> None:
    """60/min es el tope; apurarlo desde dos procesos garantiza 429."""
    cliente = DexScreenerClient()
    assert cliente._limitador("orders", 60)._max_rps < 1.0


def test_cada_endpoint_tiene_su_propio_cubo() -> None:
    """La documentacion da 60/min A CADA UNO. Compartiendo cubo, pedir los
    dos feeds se serializaba y costaba 2,5s en vez de ir en paralelo."""
    cliente = DexScreenerClient()
    boosts = cliente._limitador("boosts", 60)
    perfiles = cliente._limitador("profiles", 60)
    assert boosts is not perfiles


def test_el_cubo_de_un_endpoint_se_reutiliza() -> None:
    """Si se crease uno nuevo por llamada no limitaria nada."""
    cliente = DexScreenerClient()
    assert cliente._limitador("boosts", 60) is cliente._limitador("boosts", 60)


class _ClienteEspia(DexScreenerClient):
    """Cuenta cuantas veces se cierra: cerrarlo tira el limitador con el."""

    def __init__(self) -> None:
        super().__init__()
        self.cerrado = 0

    async def get_latest_boosts(self) -> list:
        return []

    async def get_latest_token_profiles(self) -> list:
        return []

    async def close(self) -> None:
        self.cerrado += 1


def test_el_cliente_que_se_pasa_no_se_cierra() -> None:
    """Si get_candidates lo cierra, el limitador muere con el y no limita
    nada entre vueltas: eso era exactamente el fallo de los 429."""
    from dashboard.dexpaid_feed import get_candidates

    espia = _ClienteEspia()
    for _ in range(3):
        asyncio.run(get_candidates(limit=5, dex=espia, check_locked=False))
    assert espia.cerrado == 0


def test_el_limitador_es_el_mismo_entre_vueltas() -> None:
    """La prueba de fondo: el cubo de golpes no se rellena solo por sondear.

    Con un cliente nuevo por vuelta este id cambiaba cada 5 segundos.
    """
    from dashboard.dexpaid_feed import get_candidates

    espia = _ClienteEspia()
    ids = []
    for _ in range(3):
        asyncio.run(get_candidates(limit=5, dex=espia, check_locked=False))
        ids.append(id(espia._limitador("boosts", 60)))
    assert len(set(ids)) == 1
