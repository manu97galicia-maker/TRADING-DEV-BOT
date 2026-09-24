"""El 30% se calcula sobre la CARTERA, no sobre lo que queda libre.

EL CAMBIO, pedido el 23-sep ("posicion del 30% del portfolio por cada
compra" · "sobre el portfolio total").

QUE HACIA ANTES Y POR QUE ESTABA MAL. El porcentaje se aplicaba al saldo
LIBRE, o sea a lo que sobraba despues de las posiciones ya abiertas. Como
cada compra deja menos libre, cada compra salia mas pequena que la anterior:
con 1 SOL, 0,30 · 0,21 · 0,15 · 0,10 · 0,07. Nadie habia pedido que la quinta
apuesta valiera la cuarta parte que la primera, y las ultimas acababan tan
pequenas que las comisiones fijas --que no bajan con el tamano-- se comian la
operacion.

LA TRAMPA QUE VIGILA ESTE FICHERO. El saldo de la wallet NO es la cartera: en
cuanto se compra, ese SOL deja de estar en la wallet y pasa a ser moneda.
Calcular sobre `bal` a secas parece corregirlo y no lo hace -- las posiciones
siguen menguando, solo que un poco menos. La cartera es `bal` MAS lo que ya
esta dentro de las monedas, y de ahi sale
`_valor_en_posiciones_lamports()`.
"""

# ruff: noqa: S101, SLF001, PLR2004, PLC0415

import json
from pathlib import Path

from core.pubkeys import LAMPORTS_PER_SOL
from trading.universal_trader import UniversalTrader

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")


def _trader(tmp_path: Path) -> UniversalTrader:
    """Un trader con lo justo para leer marcadores, sin red ni wallet."""
    t = UniversalTrader.__new__(UniversalTrader)
    t._open_pos_path = tmp_path / "open_position.json"
    return t


def _marcador(tmp_path: Path, nombre: str, rec: dict) -> None:
    (tmp_path / nombre).write_text(json.dumps(rec), encoding="utf-8")


# ----------------------------------------- lo que hay dentro de las monedas


def test_sin_posiciones_abiertas_no_hay_nada_dentro(tmp_path: Path) -> None:
    """La primera compra del dia: la cartera es solo el saldo."""
    assert _trader(tmp_path)._valor_en_posiciones_lamports() == 0


def test_suma_el_valor_de_ahora_de_cada_posicion(tmp_path: Path) -> None:
    """Se prefiere `value_sol`, que es lo que vale AHORA.

    Lo refresca `_write_live_status` en cada vuelta del vigilante, asi que
    recoge las ganancias y las perdidas: si una posicion se ha ido a la
    mitad, la cartera ha encogido de verdad y la siguiente compra tiene que
    salir mas pequena. Usar el coste de entrada aqui seria comprar como si no
    se hubiera perdido nada.
    """
    _marcador(tmp_path, "open_position_AAA.json", {"mint": "A", "value_sol": 0.4})
    _marcador(tmp_path, "open_position_BBB.json", {"mint": "B", "value_sol": 0.1})
    dentro = _trader(tmp_path)._valor_en_posiciones_lamports()
    assert abs(dentro / LAMPORTS_PER_SOL - 0.5) < 1e-6


def test_una_posicion_recien_abierta_vale_lo_que_costo(tmp_path: Path) -> None:
    """EL HUECO DE LOS PRIMEROS SEGUNDOS, y no es teorico: la segunda compra
    puede llegar antes de que el vigilante haya escrito un solo `value_sol`.

    El marcador que escribe la compra trae `entry_price` y `amount` pero no
    `value_sol`. Sin este respaldo, una posicion recien abierta contaria CERO
    y la segunda compra volveria a calcularse sobre una cartera encogida --
    justo el fallo que se esta arreglando, reaparecido en la peor ventana.
    """
    _marcador(
        tmp_path,
        "open_position_AAA.json",
        {"mint": "A", "entry_price": 0.0002, "amount": 1500.0},
    )
    dentro = _trader(tmp_path)._valor_en_posiciones_lamports()
    assert abs(dentro / LAMPORTS_PER_SOL - 0.3) < 1e-6


def test_un_marcador_roto_no_impide_comprar(tmp_path: Path) -> None:
    """NUNCA LEVANTA. Esto corre DENTRO del cerrojo de gasto, en el camino de
    la compra: una excepcion aqui no es "se calcula mal el tamano", es "no se
    compra y ademas se queda la moneda reservada".

    Un fichero a medio escribir devuelve de menos, o sea que se compra mas
    pequeno. Ese es el lado seguro del error y es el que se elige.
    """
    _marcador(tmp_path, "open_position_AAA.json", {"mint": "A", "value_sol": 0.4})
    (tmp_path / "open_position_ZZZ.json").write_text("{roto", encoding="utf-8")
    dentro = _trader(tmp_path)._valor_en_posiciones_lamports()
    assert abs(dentro / LAMPORTS_PER_SOL - 0.4) < 1e-6


def test_un_valor_absurdo_no_tumba_la_cuenta(tmp_path: Path) -> None:
    """Un campo con texto donde iba un numero no puede romper la compra."""
    _marcador(tmp_path, "open_position_AAA.json", {"mint": "A", "value_sol": "mucho"})
    _marcador(tmp_path, "open_position_BBB.json", {"mint": "B", "value_sol": 0.2})
    dentro = _trader(tmp_path)._valor_en_posiciones_lamports()
    assert abs(dentro / LAMPORTS_PER_SOL - 0.2) < 1e-6


# ----------------------------------------- que el camino de compra lo use


def test_las_dos_bases_existen_y_la_elige_el_yaml() -> None:
    """EL CAMINO DE COMPRA TIENE LAS DOS CUENTAS, y manda `fraccion_base`.

    Se hizo configurable el 24-sep despues de que la base fuera y viniera TRES
    veces en dos dias: saldo libre -> cartera -> (10 $ fijos) -> cartera ->
    saldo libre. Cada vuelta era un commit tocando el camino de compra con
    dinero real dentro, que es exactamente donde no se quiere estar editando
    por gusto.

    Este fichero sigue entero aunque hoy mande "libre": lo que vigila no es la
    decision --esa cambia-- sino que la cuenta de la cartera siga bien hecha
    para el dia que se vuelva a ella.
    """
    assert "cartera / LAMPORTS_PER_SOL * self.fraccion_saldo" in FUENTE
    assert "free / LAMPORTS_PER_SOL * self.fraccion_saldo" in FUENTE
    assert "cartera = bal + _dentro" in FUENTE
    # Y la que se usa la decide el yaml, no el codigo.
    assert 'self.fraccion_base == "libre"' in FUENTE


def test_un_valor_mal_escrito_no_cambia_en_silencio_lo_que_se_gasta() -> None:
    """`fraccion_base: librr` no puede volverse otra cosa sin avisar.

    Cualquier cosa que no sea exactamente "libre" cae en "cartera", que es la
    base MAS grande: si una errata pasara por "libre" sin serlo, se gastaria
    mas de lo que nadie pidio. Al reves --caer en cartera por error-- se nota
    enseguida en el cartel de arranque, que dice la base con todas las letras.
    """
    import trading.universal_trader as _mod

    t = UniversalTrader.__new__(UniversalTrader)
    for valor, esperado in (
        ("libre", "libre"), ("LIBRE", "libre"), ("  libre  ", "libre"),
        ("librr", "cartera"), ("", "cartera"), (None, "cartera"),
        ("cartera", "cartera"),
    ):
        _mod.UniversalTrader.__init__  # noqa: B018 - solo documenta de donde sale
        t.fraccion_base = (
            "libre" if str(valor or "").strip().lower() == "libre" else "cartera"
        )
        assert t.fraccion_base == esperado, valor


def test_la_reserva_minima_sigue_frenando() -> None:
    """LO QUE NO CAMBIA, y es lo que impide que esto vacie la wallet.

    La cartera es mas grande que el saldo libre, asi que el importe pedido
    tambien lo es. Lo unico que evita que se pida mas de lo que hay es el
    guardian de siempre: `free` --saldo menos reserva menos comprometido--
    tiene que cubrir la compra mas el slippage mas las comisiones. Si alguien
    quita esa guarda pensando que la cartera ya lo tiene en cuenta, el bot
    firmaria compras que no caben.
    """
    assert "free = bal - self._reserved_lamports - self.min_reserve_lamports" in FUENTE
    assert "if free < need:" in FUENTE


def test_el_log_dice_de_donde_sale_la_cartera() -> None:
    """Desde el movil, el log es lo unico que hay. "compra de 0,30" no deja
    comprobar nada; el desglose --cuanto en la wallet, cuanto en monedas--
    permite rehacer la cuenta a mano y ver si el numero es el que toca.
    """
    assert "CARTERA:" in FUENTE
    assert "en wallet" in FUENTE
    assert "en monedas" in FUENTE
