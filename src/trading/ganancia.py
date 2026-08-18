"""Bote comun de ganancia real, para que la compra crezca con lo que se gana.

QUE HACE. Guarda en un JSON el SOL que se lleva ganado de VERDAD, sumando
todas las operaciones cerradas de TODAS las estrategias. Con ese numero, el
tamano de cada compra sube un tanto por ciento por cada SOL ganado:

    importe = base * (1 + escala * ganancia_acumulada)

Con `escala = 0.35` y una base de 0,15 SOL:

    +0 SOL ganados  ->  0,150 SOL por compra
    +1 SOL ganados  ->  0,203 SOL
    +2 SOL ganados  ->  0,255 SOL
    +5 SOL ganados  ->  0,413 SOL

POR QUE UN BOTE COMUN Y NO UNO POR BOT. "Por cada Solana que ganemos" es la
caja entera, no la de cada estrategia por separado. Los tres bots comparten
wallet, asi que tambien comparten el bote.

POR QUE NO BAJA DE LA BASE. En negativo el multiplicador se queda en 1: se
sigue operando con el tamano de siempre. Reducir la apuesta tras una racha
mala suena prudente pero convierte cualquier bache en un agujero del que no se
sale, porque hace falta el doble de aciertos para recuperar con la mitad de
tamano.

POR QUE EL P&L REAL. El teorico del bot ha llegado a equivocarse DE SIGNO
--reporto +229 SOL en una operacion que dio -0,02--, y un bote alimentado con
ese numero subiria el tamano de las compras justo cuando se esta perdiendo.
Aqui solo entra lo que se ha leido de los deltas de la wallet.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

#: Fichero del bote. Compartido por los tres bots, al lado del codigo.
FICHERO = Path(__file__).resolve().parents[2] / "ganancia_real.json"


class Ganancia:
    """Bote de ganancia real acumulada, compartido entre estrategias."""

    def __init__(self, fichero: Path | str = FICHERO) -> None:
        self.fichero = Path(fichero)

    # ------------------------------------------------------------------ leer

    def acumulada(self) -> float:
        """SOL ganados de verdad hasta ahora. 0 si no hay fichero o esta roto."""
        try:
            d = json.loads(self.fichero.read_text(encoding="utf-8"))
            return float(d.get("sol", 0.0))
        except Exception:  # noqa: BLE001
            return 0.0

    # --------------------------------------------------------------- escribir

    def suma(self, pnl_real_sol: float) -> float:
        """Apunta una operacion cerrada y devuelve el nuevo total.

        Se relee justo antes de escribir para no pisar lo que hayan apuntado
        los otros dos bots entre medias. No es un candado de verdad: si dos
        cierran en el mismo instante puede perderse una suma. El bote solo
        decide el tamano de la siguiente compra, asi que equivocarse por una
        operacion no rompe nada.
        """
        try:
            total = self.acumulada() + float(pnl_real_sol)
        except (TypeError, ValueError):
            return self.acumulada()
        try:
            self.fichero.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.fichero.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"sol": total}, fh, indent=1)
            os.replace(tmp, self.fichero)
        except Exception:  # noqa: BLE001
            logger.warning("bote: no se pudo guardar la ganancia acumulada")
        return total

    # ---------------------------------------------------------------- aplicar

    def multiplicador(self, escala: float) -> float:
        """Cuanto se multiplica la compra base. Nunca por debajo de 1."""
        if escala <= 0:
            return 1.0
        return 1.0 + escala * max(0.0, self.acumulada())
