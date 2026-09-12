"""Durable Pump.fun callout outbox populated after confirmed buys.

Pump.fun does not currently document a public write API for callouts.  This
module therefore prepares compliant messages after a confirmed purchase and
stores them durably.  A future authorised publisher can consume this outbox
without coupling authentication or browser cookies to the trading loop.
"""

from __future__ import annotations

# OJO al importar fechas: `datetime.UTC` es de Python 3.11, pero este
# proyecto declara requires-python = ">=3.10" y el droplet corre 3.10. Alli
# ese import reventaba y se perdia el callout de CADA compra.
# `timezone.utc` es lo mismo y existe desde siempre.
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_OUTBOX = Path("trades") / "pumpfun_callout_outbox.jsonl"


def build_callout(name: str, mint: str) -> str:
    """Build the configured callout with the required position disclosure."""
    display_name = name.strip() or "Unknown token"
    return (
        f"AI opportunity detected: {display_name}\n"
        f"Contract address: {mint}\n"
        "Disclosure: this automated account holds a position in this token. "
        "Not financial advice."
    )


def enqueue_callout(
    name: str,
    mint: str,
    tx_signature: str | None,
    outbox: Path = DEFAULT_OUTBOX,
    signal_strength: float = 0.0,
) -> str:
    """Append one confirmed-buy callout to the durable JSONL outbox."""
    message = build_callout(name, mint)
    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mint": mint,
        "name": name,
        "buy_signature": tx_signature,
        "message": message,
        # Con que senal entro: es lo que decide cual de las encoladas se
        # publica cuando solo cabe una cada 6 horas.
        "signal_strength": signal_strength,
        "published_at": None,
        "status": "pending_authorized_publisher",
    }
    try:
        outbox.parent.mkdir(parents=True, exist_ok=True)
        with outbox.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("Could not persist Pump.fun callout for %s", mint)
        raise
    logger.info("Pump.fun callout queued after confirmed buy: %s", mint)
    return message


VENTANA_HORAS = 6


def _leer(outbox: Path) -> list[dict]:
    """Lee la cola entera, saltando lineas rotas."""
    if not outbox.exists():
        return []
    entradas: list[dict] = []
    for linea in outbox.read_text(encoding="utf-8").splitlines():
        try:
            dato = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(dato, dict):
            entradas.append(dato)
    return entradas


def elegir_para_publicar(
    outbox: Path = DEFAULT_OUTBOX,
    ahora: datetime | None = None,
    ventana_horas: float = VENTANA_HORAS,
) -> dict | None:
    """Devuelve el callout que toca publicar, o None si no toca.

    Pump.fun solo admite UNA llamada cada 6 horas por cuenta, y desde agosto
    reparte las recompensas primando calidad sobre cantidad. Con esas dos
    reglas, vaciar la cola es justo lo que no hay que hacer: de cada ventana
    solo cabe una, asi que hay que ELEGIR.

    Se elige la de mayor senal (el boost o el Nitro que la disparo) entre las
    que no se han publicado, y a igualdad la mas reciente. Si en la ventana ya
    se publico algo, no toca y se devuelve None.

    Args:
        outbox: Fichero JSONL de la cola.
        ahora: Momento de referencia, para poder probarlo.
        ventana_horas: Horas entre llamadas que permite pump.fun.

    Returns:
        La entrada elegida, o None.
    """
    entradas = _leer(outbox)
    if not entradas:
        return None
    ahora = ahora or datetime.now(timezone.utc)
    limite = ahora - timedelta(hours=ventana_horas)

    for entrada in entradas:
        publicado = entrada.get("published_at")
        if not publicado:
            continue
        try:
            cuando = datetime.fromisoformat(str(publicado))
        except ValueError:
            continue
        if cuando > limite:
            return None  # la ventana sigue ocupada

    pendientes = [e for e in entradas if not e.get("published_at")]
    if not pendientes:
        return None
    return max(
        pendientes,
        key=lambda e: (float(e.get("signal_strength") or 0), str(e.get("created_at"))),
    )
