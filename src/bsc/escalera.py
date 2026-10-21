"""La escalera de salida del bot BSC, tal y como la pide `INSTRUCCIONES.md`.

TODO ESTO ES ARITMETICA PURA Y SIN RED a proposito: es la parte donde un error
se paga en dinero, asi que tiene que poder comprobarse entera sin nodo, sin
GMGN y sin wallet.

LA ESCALERA PEDIDA, al pie de la letra:

  - Al subir 60%: vende el 62,5% de los tokens ORIGINALES. Eso recupera el
    capital inicial antes de comisiones (0,625 x 1,60 = 1,00).
  - Otro 27,5% repartido al alcanzar market caps de 10k, 15k, 20k, 25k, 30k
    y 35k USD. Seis escalones -> 27,5 / 6 por escalon.
  - El 10% restante: 5% en 180k, 2,5% en 400k y 2,5% en 900k.
  - Stop loss: -38% desde el precio ejecutado.

DOS DISPARADORES DISTINTOS, y es la trampa de esta escalera: el primer
escalon mira la SUBIDA DEL PRECIO (+60%) y los demas miran el MARKET CAP
absoluto (10k, 180k...). No son lo mismo y no se pueden mezclar en una sola
lista ordenada por ganancia, porque el market cap al que equivale un +60%
depende de donde se haya comprado.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Disparo(Enum):
    """Que mira un escalon para decidir si le toca."""

    SUBIDA = "subida"        # ganancia sobre el precio de compra
    MARKET_CAP = "market_cap"  # market cap absoluto en USD


@dataclass(frozen=True)
class Escalon:
    """Un peldano de la escalera.

    Attributes:
        disparo: si se mide por subida de precio o por market cap.
        umbral: +0,60 para una subida del 60%; 10_000 para un market cap.
        vender: fraccion de los tokens ORIGINALES que se vende aqui.
        nombre: para los logs y el estado persistente.
    """

    disparo: Disparo
    umbral: float
    vender: float
    nombre: str


#: Los seis escalones de market cap que reparten el 27,5%.
_MC_INTERMEDIOS = (10_000.0, 15_000.0, 20_000.0, 25_000.0, 30_000.0, 35_000.0)

#: El 10% que se conserva, y donde se suelta.
_MC_MOONBAG = ((180_000.0, 0.050), (400_000.0, 0.025), (900_000.0, 0.025))


def construir_escalera() -> list[Escalon]:
    """Devuelve la escalera completa, en el orden en que se evalua.

    El reparto del 27,5% entre seis escalones da 4,5833...% cada uno. NO se
    redondea: redondear a 4,58 dejaria un 0,02% colgado para siempre, y
    redondear a 4,59 intentaria vender mas tokens de los que quedan. Se
    guarda la fraccion exacta y el ultimo escalon absorbe el resto por
    construccion, porque la suma se comprueba contra 1,0 en los tests.
    """
    escalones = [
        Escalon(Disparo.SUBIDA, 0.60, 0.625, "recupera capital (+60%)"),
    ]
    reparto = 0.275 / len(_MC_INTERMEDIOS)
    escalones += [
        Escalon(Disparo.MARKET_CAP, mc, reparto, f"MC {mc / 1000:.0f}k")
        for mc in _MC_INTERMEDIOS
    ]
    escalones += [
        Escalon(Disparo.MARKET_CAP, mc, frac, f"moonbag MC {mc / 1000:.0f}k")
        for mc, frac in _MC_MOONBAG
    ]
    return escalones


def fraccion_total(escalera: list[Escalon]) -> float:
    """Cuanto de la posicion original llega a venderse si se cumplen todos."""
    return sum(e.vender for e in escalera)


def escalones_pendientes(
    escalera: list[Escalon],
    cobrados: set[str],
    ganancia: float,
    market_cap_usd: float,
) -> list[Escalon]:
    """Escalones que TOCAN ahora y que no se han cobrado antes.

    Args:
        escalera: la escalera completa.
        cobrados: nombres de los escalones ya ejecutados (viene del estado
            persistente, para no repetirlos tras un reinicio).
        ganancia: (precio_actual / precio_compra) - 1.
        market_cap_usd: market cap actual en USD.

    Returns:
        Los escalones cumplidos y aun sin cobrar, en orden de escalera.

    SE DEVUELVEN TODOS LOS QUE TOQUEN, no solo el siguiente: una vela puede
    saltarse tres escalones de golpe, y cobrar solo uno dejaria la posicion
    vendiendo tarde el resto. Es lo que pasa cuando el precio se mueve mas
    rapido que el bucle de vigilancia.
    """
    tocan = []
    for e in escalera:
        if e.nombre in cobrados:
            continue
        valor = ganancia if e.disparo is Disparo.SUBIDA else market_cap_usd
        if valor >= e.umbral:
            tocan.append(e)
    return tocan


def tokens_a_vender(
    escalones: list[Escalon], tokens_originales: float, tokens_restantes: float
) -> float:
    """Cuantos tokens vender por esos escalones, sin pasarse de lo que queda.

    `vender` es fraccion del ORIGINAL, no de lo que queda. Si varios escalones
    se cumplen a la vez sus fracciones se suman, y el resultado se recorta a
    los tokens que realmente hay: sin ese recorte una venta pediria mas
    tokens de los que tiene la wallet y la transaccion revertiria, dejando la
    posicion entera sin vender en vez de vender lo que se podia.
    """
    pedidos = sum(e.vender for e in escalones) * tokens_originales
    return max(0.0, min(pedidos, tokens_restantes))


def precio_de_stop(precio_entrada: float, stop_pct: float = 0.38) -> float:
    """Precio al que salta el stop loss, medido desde el precio EJECUTADO.

    `INSTRUCCIONES.md` dice "-38% desde el precio ejecutado", no desde el
    precio que se vio al decidir la compra. Con el slippage de estas monedas
    la diferencia es real, y medir desde el precio visto daria un stop mas
    abajo de lo pedido sin que nadie lo notara.
    """
    return precio_entrada * (1.0 - stop_pct)
