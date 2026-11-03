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
import sqlite3
from contextlib import contextmanager
from itertools import product
from random import SystemRandom
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_OUTBOX = Path("trades") / "pumpfun_callout_outbox.jsonl"

INTRODUCTIONS = (
    "AI opportunity detected",
    "Fresh market signal spotted",
    "New token on the AI radar",
    "Entry candidate identified",
    "Market activity worth watching",
    "A new setup has entered the watchlist",
    "Fresh opportunity flagged for review",
    "New market candidate detected",
    "Signal check complete, new candidate found",
    "A fresh setup is on the radar",
    "Another market signal is in focus",
    "New token activity flagged",
    "Opportunity scan, candidate found",
    "New setup ready for observation",
    "Market radar picked up a new candidate",
    "Fresh entry signal logged",
    "An observed setup passed the entry checks",
    "New candidate added to the market watch",
    "Signal radar detected fresh activity",
    "A new token setup is in focus",
    "Fresh candidate marked for tracking",
    "Market scan identified a new setup",
    "Another entry candidate is on the watchlist",
    "New signal queued for observation",
)


def choose_intro(used) -> str:
    """Random unused wording; persistent callers supply all historical intros."""
    used = set(used)
    available = [line for line in INTRODUCTIONS if line not in used]
    if not available:
        available = [
            f"{subject} {verb} {target}"
            for subject, verb, target in product(
                ("AI radar", "Market scan", "Signal monitor", "Token watch",
                 "Setup tracker", "Entry scanner", "Watchlist monitor", "Market radar"),
                ("flagged", "identified", "picked up", "logged",
                 "spotted", "recorded", "marked", "highlighted"),
                ("a fresh candidate", "a new setup", "an entry signal", "new activity",
                 "another market candidate", "a token setup", "a candidate for review",
                 "a new observation candidate"),
            )
            if f"{subject} {verb} {target}" not in used
        ]
    if available:
        return SystemRandom().choice(available)
    # Exhaustion never cycles back through an already used sentence.
    serial = len(used) + 1
    while f"New market candidate logged for review number {serial}" in used:
        serial += 1
    return f"New market candidate logged for review number {serial}"


def build_callout(
    name: str, mint: str, *, intro: str = INTRODUCTIONS[0], paper: bool = False
) -> str:
    """Build the configured callout with the required position disclosure."""
    display_name = " ".join(name.split()) or "Unknown token"
    disclosure = (
        "PAPER simulation: no real purchase was made. Not financial advice."
        if paper else
        "Disclosure: this automated account holds a position in this token. "
        "Not financial advice."
    )
    return (
        f"{'PAPER | ' if paper else ''}{intro}: {display_name}\n"
        f"Contract address: {mint}\n"
        f"{disclosure}"
    )


def enqueue_callout(
    name: str,
    mint: str,
    tx_signature: str | None,
    outbox: Path = DEFAULT_OUTBOX,
    signal_strength: float = 0.0,
) -> str:
    """Append one confirmed-buy callout to the durable JSONL outbox."""
    outbox = Path(outbox)
    try:
        with _outbox_lock(outbox):
            return _enqueue_locked(name, mint, tx_signature, outbox, signal_strength)
    except (OSError, sqlite3.Error):
        logger.exception("Could not persist Pump.fun callout for %s", mint)
        raise


@contextmanager
def _outbox_lock(outbox: Path):
    """Serialize phrase allocation and append across bots and restarts."""
    outbox.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(outbox) + ".lock.sqlite3", timeout=2)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            yield
    finally:
        db.close()


def _enqueue_locked(name, mint, tx_signature, outbox, signal_strength):
    previous = _leer(outbox)
    for entry in previous:
        if str(entry.get("mint")) == mint and entry.get("message"):
            return str(entry["message"])
    used = {
        entry.get("intro") or str(entry.get("message", "")).split(":", 1)[0]
        for entry in previous
    }
    intro = choose_intro(used)
    message = build_callout(name, mint, intro=intro)
    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mint": mint,
        "name": name,
        "buy_signature": tx_signature,
        "message": message,
        "intro": intro,
        # Con que senal entro: es lo que decide cual de las encoladas se
        # publica cuando solo cabe una cada 6 horas.
        "signal_strength": signal_strength,
        "published_at": None,
        "status": "pending_authorized_publisher",
    }
    with outbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info("Pump.fun callout queued after confirmed buy: %s", mint)
    return message


# Sin tope de cantidad. Aqui habia una ventana de 6 horas que me invente:
# el FAQ oficial dice "There is no limit to callouts or your earnings". Lo
# que SI cambio pump.fun en agosto es como reparte, primando "Quality >>>
# Quantity", asi que el freno correcto no es el reloj sino la calidad.
#
# 0 = publicar cualquier cosa pendiente. Subelo para exigir que alguien
# haya pagado de verdad por la moneda antes de gastar un callout en ella:
# la senal es el boost de DexScreener o el Nitro de DEXTools, y 500 es el
# escalon que la propia estrategia considera promocion cara.
SENAL_MINIMA = 0.0


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
    senal_minima: float = SENAL_MINIMA,
) -> dict | None:
    """Devuelve el mejor callout pendiente, o None si no hay ninguno digno.

    Pump.fun no limita cuantos callouts puedes hacer, pero reparte el bote
    pro-rata entre los callers segun el volumen que genere cada uno, y desde
    agosto pesa mas la calidad que la cantidad. Con esas reglas, publicar
    todo lo que cae es tan malo como no publicar: cada callout flojo baja tu
    media sin traer volumen.

    Asi que se elige la de mayor senal -- lo que alguien pago por promocionar
    la moneda, que es el unico indicador objetivo de traccion que tenemos --
    y se descarta lo que no llegue al suelo pedido.

    Una moneda ya llamada no se repite: el segundo callout de lo mismo no
    trae seguidores nuevos y gasta turno.

    Args:
        outbox: Fichero JSONL de la cola.
        senal_minima: Suelo de senal para que merezca la pena publicar.

    Returns:
        La entrada elegida, o None si no hay nada que cumpla.
    """
    entradas = _leer(outbox)
    if not entradas:
        return None
    ya_llamadas = {str(e.get("mint")) for e in entradas if e.get("published_at")}
    candidatas = [
        e
        for e in entradas
        if not e.get("published_at")
        and str(e.get("mint")) not in ya_llamadas
        and float(e.get("signal_strength") or 0) >= senal_minima
    ]
    if not candidatas:
        return None
    return max(
        candidatas,
        key=lambda e: (float(e.get("signal_strength") or 0), str(e.get("created_at"))),
    )
