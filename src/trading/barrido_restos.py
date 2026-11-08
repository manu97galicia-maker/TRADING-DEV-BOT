"""BARRIDO DE RESTOS: que no queden saldos pequenos en la wallet.

Pedido el 8-nov: "revisa cada poco que no queden pendientes saldos pequenos"
-> eligio "cada 10 min".

Cada pasada lee TODAS las cuentas de tokens de la wallet y decide, una por
una, sin tocar nada que pueda valer dinero:

    vacia (0 tokens)            -> se cierra; vuelve el alquiler (~0,002 SOL)
    vale menos de `max_usd`     -> se queman los tokens y se cierra
    vale `max_usd` o mas        -> no se toca
    sin precio                  -> no se toca (no saber no es "vale poco")
    posicion abierta de ALGUNA  -> no se toca (lee las fichas open_position*
    estrategia                     de la carpeta, las de las tres)
    SOL envuelto (WSOL)         -> no se toca nunca: es SOL

Aqui solo viven las DECISIONES, sin red, para poder probarlas. Leer la wallet,
pedir precios y firmar lo hace el trader (`UniversalTrader._barrer_restos`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 - se usa en tiempo de ejecucion

#: El SOL envuelto. Su cuenta guarda SOL de verdad: jamas se quema.
WSOL = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class Cuenta:
    """Una cuenta de tokens de la wallet, tal como la da el nodo."""

    direccion: str
    mint: str
    crudo: int
    decimales: int
    programa: str


def cuentas_de_la_respuesta(respuesta: dict | None, programa: str) -> list[Cuenta]:
    """Saca las cuentas de un `getTokenAccountsByOwner` con jsonParsed."""
    out: list[Cuenta] = []
    for item in ((respuesta or {}).get("result") or {}).get("value") or []:
        try:
            info = item["account"]["data"]["parsed"]["info"]
            cantidad = info["tokenAmount"]
            out.append(
                Cuenta(
                    direccion=str(item["pubkey"]),
                    mint=str(info["mint"]),
                    crudo=int(cantidad["amount"]),
                    decimales=int(cantidad["decimals"]),
                    programa=programa,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def mints_protegidos(carpeta: Path) -> set[str]:
    """Mints con ficha de posicion abierta, de CUALQUIER estrategia.

    Se cuentan todas las fichas, tambien las viejas: una ficha que nadie
    renueva puede ser una posicion huerfana que aun vale dinero, y el barrido
    no esta para decidir eso.
    """
    out: set[str] = set()
    for f in carpeta.glob("open_position*.json"):
        if "__" in f.stem:
            out.add(f.stem.rsplit("__", 1)[1])
        try:
            dato = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(dato, dict) and dato.get("mint"):
            out.add(str(dato["mint"]))
    return out


def decide(
    cuenta: Cuenta,
    protegidos: set[str],
    valor_usd: float | None,
    max_usd: float,
) -> str:
    """Que hacer con una cuenta: "cerrar", "quemar" o "dejar"."""
    if cuenta.mint == WSOL or cuenta.mint in protegidos:
        return "dejar"
    if cuenta.crudo <= 0:
        return "cerrar"
    if valor_usd is None or valor_usd < 0:
        return "dejar"
    if valor_usd < max_usd:
        return "quemar"
    return "dejar"
