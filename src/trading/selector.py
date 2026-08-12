"""Elige sola la estrategia que mas gana, y apaga las demas.

QUE HACE. Cada estrategia opera en paralelo con el mismo tamaño (0,15 SOL).
Cuando TODAS llevan al menos ``MINIMO_OPERACIONES`` cerradas, se compara su
retorno real y se deja viva unicamente la mejor. A partir de ahi el resto no
compran mas.

POR QUE CON EL P&L REAL Y NO CON EL TEORICO. El numero teorico del bot ha
llegado a equivocarse DE SIGNO: en `degen` apunto haber cobrado 0,1496 SOL
cuando la cadena dice que no vendio nada y se perdieron los 0,1687 enteros. Un
selector alimentado con ese numero elegiria la estrategia que peor va. Aqui
solo entra lo que se ha leido de los deltas de la wallet.

POR QUE 10 Y NO 3. Con 10 operaciones y un acierto real del 40%, la
probabilidad de que la peor estrategia parezca la mejor por azar sigue siendo
alta; con 3 es practicamente una moneda al aire. Diez es el minimo con el que
la comparacion dice algo, y aun asi conviene mirarla a mano antes de fiarse.

COMO SE USA. Tres lineas en el bot:

    from trading.selector import Selector

    sel = Selector()                                  # al arrancar
    if not sel.puede_operar(NOMBRE_ESTRATEGIA):       # antes de comprar
        return
    sel.apunta(NOMBRE_ESTRATEGIA, pnl_real_sol)       # al cerrar cada posicion

El estado vive en un JSON, asi que sobrevive a los reinicios -- que es
imprescindible: el bot se reinicia varias veces al dia y un contador en memoria
volveria a cero cada vez y no llegaria nunca a las 10.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

#: Operaciones cerradas que necesita CADA estrategia antes de comparar.
MINIMO_OPERACIONES = 10

#: Fichero de estado. Al lado del bot para que se vea de un vistazo.
FICHERO = Path(__file__).resolve().parents[2] / "selector_estado.json"


class Selector:
    """Contador de resultados reales por estrategia, con eleccion automatica."""

    def __init__(self, fichero: Path | str = FICHERO,
                 minimo: int = MINIMO_OPERACIONES) -> None:
        self.fichero = Path(fichero)
        self.minimo = int(minimo)
        self.datos: dict = self._cargar()

    # ---------------------------------------------------------------- estado

    def _cargar(self) -> dict:
        if not self.fichero.exists():
            return {"estrategias": {}, "ganadora": None}
        try:
            d = json.loads(self.fichero.read_text(encoding="utf-8"))
            d.setdefault("estrategias", {})
            d.setdefault("ganadora", None)
            return d
        except Exception:  # noqa: BLE001
            # Un JSON roto no puede parar el bot, pero tampoco se puede
            # sobrescribir en silencio: se aparta con otro nombre y se empieza
            # de cero, dejando rastro de que paso.
            roto = self.fichero.with_suffix(".roto.json")
            try:
                self.fichero.replace(roto)
                logger.warning("selector: estado ilegible, apartado en %s", roto)
            except Exception:  # noqa: BLE001
                pass
            return {"estrategias": {}, "ganadora": None}

    def _guardar(self) -> None:
        # Escritura atomica: primero a un temporal y luego un rename. Si el bot
        # muere a mitad, el fichero viejo sigue entero en vez de quedar cortado.
        self.fichero.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.fichero.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.datos, fh, indent=1, ensure_ascii=False)
            os.replace(tmp, self.fichero)
        except Exception:  # noqa: BLE001
            logger.warning("selector: no se pudo guardar el estado")
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------- registro

    def apunta(self, estrategia: str, pnl_real_sol: float,
               coste_sol: float = 0.15) -> None:
        """Registra una operacion CERRADA con su resultado REAL de la cadena.

        ``pnl_real_sol`` es la variacion neta de la wallet: negativo si se
        perdio. NO se le puede pasar el P&L teorico del bot.
        """
        e = self.datos["estrategias"].setdefault(
            estrategia, {"n": 0, "sol": 0.0, "coste": 0.0,
                         "ganadas": 0, "peor": 0.0})
        e["n"] += 1
        e["sol"] += float(pnl_real_sol)
        e["coste"] += float(coste_sol)
        if pnl_real_sol > 0:
            e["ganadas"] += 1
        e["peor"] = min(e["peor"], float(pnl_real_sol))
        self._decidir()
        self._guardar()
        logger.info(
            "selector: %s lleva %d operaciones, %+0.4f SOL (ROI %+0.1f%%)",
            estrategia, e["n"], e["sol"], 100 * self.roi(estrategia),
        )

    def roi(self, estrategia: str) -> float:
        """Retorno sobre lo arriesgado. 0 si esa estrategia no ha operado."""
        e = self.datos["estrategias"].get(estrategia)
        if not e or e["coste"] <= 0:
            return 0.0
        return e["sol"] / e["coste"]

    # -------------------------------------------------------------- decision

    def _decidir(self) -> None:
        if self.datos["ganadora"]:
            return
        es = self.datos["estrategias"]
        # Hacen falta al menos dos estrategias: elegir "la mejor" de una sola
        # no es elegir, es apagar el bot cuando le toque perder.
        if len(es) < 2 or any(v["n"] < self.minimo for v in es.values()):
            return
        mejor = max(es, key=self.roi)
        if self.roi(mejor) <= 0:
            # Todas pierden. Elegir la que pierde menos seria seguir perdiendo
            # con mejor cara; se para todo y que decida una persona.
            self.datos["ganadora"] = "__ninguna__"
            logger.warning(
                "selector: las %d estrategias pierden con %d operaciones cada "
                "una. Se paran TODAS. ROIs: %s", len(es), self.minimo,
                {k: f"{100*self.roi(k):+.1f}%" for k in es},
            )
            return
        self.datos["ganadora"] = mejor
        logger.info(
            "selector: GANA %s con ROI %+0.1f%%. Se apagan las demas: %s",
            mejor, 100 * self.roi(mejor),
            {k: f"{100*self.roi(k):+.1f}%" for k in es if k != mejor},
        )

    def puede_operar(self, estrategia: str) -> bool:
        """False si ya se eligio ganadora y no es esta."""
        g = self.datos.get("ganadora")
        if g is None:
            return True
        return g == estrategia

    def resumen(self) -> str:
        es = self.datos["estrategias"]
        if not es:
            return "selector: todavia no hay operaciones"
        filas = [
            f"   {k:<28}{v['n']:>4} ops  {v['sol']:>+8.4f} SOL  "
            f"ROI {100*self.roi(k):>+7.1f}%  "
            f"{v['ganadas']}/{v['n']} ganadas  peor {v['peor']:+.4f}"
            for k, v in sorted(es.items(), key=lambda x: -self.roi(x[0]))
        ]
        g = self.datos.get("ganadora")
        cabecera = (
            f"GANADORA: {g}" if g and g != "__ninguna__"
            else ("TODAS PARADAS (todas pierden)" if g == "__ninguna__"
                  else f"midiendo (faltan {self.minimo} por estrategia)")
        )
        return cabecera + "\n" + "\n".join(filas)
