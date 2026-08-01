"""Avisos por Telegram de cada moneda MAYHEM que nace en pump.fun.

Por que existe
--------------
El bot MAYHEM solo compra a los 93 devs de `top_devs_mayhem.json`, y ahora mismo
esta apagado. Todo lo demas pasa delante sin que nadie se entere. Esto escucha
las mayhem 24/7 y te las manda al Telegram para que puedas mirarlas (o entrar a
mano) aunque el bot no compre.

De donde sale el dato
---------------------
La bandera `is_mayhem_mode` NO viene en el feed gratis de PumpPortal: solo esta
en el `CreateEvent` de la propia transaccion. Por eso esto se engancha al WSS de
Helius con el mismo listener de `logs` que usa el bot, y no al feed de scout.py.

Dos velocidades, para no acabar silenciando el chat
---------------------------------------------------
1. **Dev de la lista** -> aviso INMEDIATO, con su historial (cuantas mayhem ha
   lanzado, cuantas pasaron de $30K, su pico mediano). Es el aviso que sirve
   para entrar a mano en caliente.
2. **Dev desconocido** -> no se avisa al nacer (salen demasiadas). Se mira su
   market cap a los `MAYHEM_ESPERA_S` segundos y solo se avisa si ya paso de
   `MAYHEM_UMBRAL_MC`. Asi solo llegan las que se estan moviendo de verdad.

Ajustes (variables de entorno, todas opcionales)
------------------------------------------------
    MAYHEM_AVISAR_TODAS=false   true = avisa TODA mayhem al nacer (ruidoso)
    MAYHEM_ESPERA_S=25          segundos antes de mirar el MC de un desconocido
    MAYHEM_UMBRAL_MC=12000      $ de market cap para avisar de un desconocido
    MAYHEM_RESUMEN_H=6          cada cuantas horas manda el recuento
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID   (si faltan, solo escribe por consola)

Arranque
--------
    cd /root/Definitivo_bot
    ./.venv/bin/python alerta_mayhem.py

No toca la wallet ni compra nada: es solo un vigia.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent / "src"))

import aiohttp

from interfaces.core import Platform, TokenInfo
from monitoring.universal_logs_listener import UniversalLogsListener

PUMPFUN_API = "https://frontend-api-v3.pump.fun/coins"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _load_dotenv() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

AVISAR_TODAS = _env("MAYHEM_AVISAR_TODAS", "false").lower() in ("1", "true", "yes")
ESPERA_S = float(_env("MAYHEM_ESPERA_S", "25"))
UMBRAL_MC = float(_env("MAYHEM_UMBRAL_MC", "12000"))
RESUMEN_H = float(_env("MAYHEM_RESUMEN_H", "6"))

WSS = _env("SOLANA_NODE_WSS_ENDPOINT")

#: Cuantos mints recordar para no avisar dos veces del mismo. Con ~50 mayhem/h
#: sobra de largo, y evita que el proceso crezca sin limite en dias.
MEMORIA_MINTS = 5000


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Telegram                                                                    #
# --------------------------------------------------------------------------- #


async def avisar(session: aiohttp.ClientSession, texto: str) -> None:
    """Manda un mensaje. Un fallo de Telegram nunca puede tumbar el vigia."""
    token, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("(sin Telegram) " + texto.replace("\n", " | ")[:160])
        return
    try:
        async with session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat,
                "text": texto[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                log(f"telegram {r.status}: {(await r.text())[:120]}")
    except Exception as exc:  # noqa: BLE001 -- avisar nunca debe romper el bucle
        log(f"telegram fallo: {exc}")


# --------------------------------------------------------------------------- #
# Historial de devs                                                           #
# --------------------------------------------------------------------------- #


def cargar_devs() -> dict[str, dict[str, Any]]:
    """Wallet -> estadisticas de Dune, de la misma lista que usa el bot MAYHEM."""
    try:
        datos = json.loads(Path("top_devs_mayhem.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"no pude leer top_devs_mayhem.json: {exc}")
        return {}
    out = {}
    for w in datos.get("wallets", []):
        if isinstance(w, dict) and w.get("wallet"):
            out[w["wallet"]] = w
        elif isinstance(w, str):
            out[w] = {}
    return out


def cargar_vetados() -> set[str]:
    try:
        return set(
            json.loads(Path("blacklist_devs.json").read_text(encoding="utf-8"))[
                "wallets"
            ]
        )
    except Exception:  # noqa: BLE001
        return set()


def ficha_dev(s: dict[str, Any]) -> str:
    """Linea de historial del dev, que es lo unico parecido a una expectativa."""
    if not s:
        return ""
    n = s.get("mayhem_launches", 0)
    p30 = s.get("pct_pump_30k", 0)
    p100 = s.get("pct_pump_100k", 0)
    med = s.get("median_peak", 0)
    tier = s.get("_tier", "")
    return (
        f"\n📈 Historial: {n} mayhem · {p30:.0f}% pasaron $30K · "
        f"{p100:.0f}% pasaron $100K\n"
        f"   pico mediano ${med:,.0f}" + (f" · {tier}" if tier else "")
    )


# --------------------------------------------------------------------------- #
# Market cap                                                                  #
# --------------------------------------------------------------------------- #


async def market_cap(session: aiohttp.ClientSession, mint: str) -> float | None:
    try:
        async with session.get(
            f"{PUMPFUN_API}/{mint}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                return None
            return float((await r.json()).get("usd_market_cap") or 0)
    except Exception:  # noqa: BLE001
        return None


def limpio(texto: str, corte: int = 60) -> str:
    """Escapa el nombre/simbolo del token: los pone el creador, no nosotros.

    Sin esto, una moneda llamada `<b>` o `Tom & Jerry` hace que Telegram rechace
    el mensaje entero (400 por HTML mal formado) y el aviso no llega.
    """
    return html.escape(texto[:corte])


def enlaces(mint: str) -> str:
    return (
        f'\n📊 <a href="https://dexscreener.com/solana/{mint}">DexScreener</a> · '
        f'<a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
        f'<a href="https://pump.fun/coin/{mint}">pump.fun</a>\n'
        f'🤖 <a href="https://t.me/MaestroSniperBot?start={mint}">Comprar en Maestro</a>\n'
        f"CA: <code>{mint}</code>"
    )


# --------------------------------------------------------------------------- #
# Avisos                                                                      #
# --------------------------------------------------------------------------- #


async def aviso_dev_conocido(
    session: aiohttp.ClientSession, t: TokenInfo, stats: dict[str, Any], cuenta: dict
) -> None:
    cuenta["conocidas"] += 1
    await avisar(
        session,
        f"🔥 <b>MAYHEM DE TU LISTA</b> · <b>{limpio(t.symbol, 16)}</b>\n"
        f"{limpio(t.name)}\n"
        f"👤 dev <code>{t.creator}</code>"
        f"{ficha_dev(stats)}"
        f"{enlaces(str(t.mint))}",
    )


async def vigilar_desconocido(
    session: aiohttp.ClientSession, t: TokenInfo, cuenta: dict
) -> None:
    """Espera un poco y solo avisa si la moneda ya se esta moviendo.

    Sin esta espera llegarian docenas de mensajes muertos al dia: casi toda
    mayhem nace y muere en el mismo minuto.
    """
    await asyncio.sleep(ESPERA_S)
    mc = await market_cap(session, str(t.mint))
    if mc is None or mc < UMBRAL_MC:
        return
    cuenta["despegaron"] += 1
    await avisar(
        session,
        f"🚀 <b>MAYHEM SUBIENDO</b> · <b>{limpio(t.symbol, 16)}</b>\n"
        f"{limpio(t.name)}\n"
        f"💰 ${mc:,.0f} de market cap a los {ESPERA_S:.0f}s\n"
        f"👤 dev <code>{t.creator}</code> (fuera de tu lista)"
        f"{enlaces(str(t.mint))}",
    )


class Vigia:
    """Estado del vigia: que devs mira, que mints ya aviso y el recuento."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.devs = cargar_devs()
        self.vetados = cargar_vetados()
        self.vistos: set[str] = set()
        self.orden: list[str] = []
        #: Las tareas de vigilancia hay que sujetarlas: asyncio solo guarda una
        #: referencia debil, y una tarea que nadie retiene puede desaparecer a
        #: media espera -- el aviso no llegaria nunca.
        self.tareas: set[asyncio.Task] = set()
        self.cuenta = {
            "mayhem": 0,
            "conocidas": 0,
            "despegaron": 0,
            "desde": time.time(),
        }

    def _es_nueva(self, mint: str) -> bool:
        """Falso si ya avisamos de este mint. Olvida los mas viejos."""
        if mint in self.vistos:
            return False
        self.vistos.add(mint)
        self.orden.append(mint)
        if len(self.orden) > MEMORIA_MINTS:
            self.vistos.discard(self.orden.pop(0))
        return True

    def _en_segundo_plano(self, corutina: Any) -> None:
        tarea = asyncio.create_task(corutina)
        self.tareas.add(tarea)
        tarea.add_done_callback(self.tareas.discard)

    async def al_detectar(self, t: TokenInfo) -> None:
        """Callback del listener: decide si esta mayhem merece un mensaje."""
        if not getattr(t, "is_mayhem_mode", False):
            return
        mint = str(t.mint)
        if not self._es_nueva(mint):
            return

        self.cuenta["mayhem"] += 1
        creador = str(t.creator)
        log(f"mayhem {t.symbol} · {creador[:6]}… · {mint}")

        if creador in self.vetados:
            return
        if creador in self.devs:
            await aviso_dev_conocido(self.session, t, self.devs[creador], self.cuenta)
        elif AVISAR_TODAS:
            self.cuenta["despegaron"] += 1
            await avisar(
                self.session,
                f"⚡ <b>MAYHEM</b> · <b>{limpio(t.symbol, 16)}</b>\n{limpio(t.name)}\n"
                f"👤 dev <code>{creador}</code>{enlaces(mint)}",
            )
        else:
            # en segundo plano: no podemos bloquear el listener ESPERA_S segundos
            self._en_segundo_plano(vigilar_desconocido(self.session, t, self.cuenta))

    async def resumen(self) -> None:
        """Recuento periodico: sin esto no se sabe si el vigia sigue vivo."""
        c = self.cuenta
        horas = (time.time() - c["desde"]) / 3600
        await avisar(
            self.session,
            f"📊 <b>Resumen mayhem</b> · ultimas {horas:.1f}h\n"
            f"Mayhem vistas: {c['mayhem']}\n"
            f"De tus devs: {c['conocidas']}\n"
            f"Desconocidas que despegaron: {c['despegaron']}",
        )
        c.update(mayhem=0, conocidas=0, despegaron=0, desde=time.time())


# --------------------------------------------------------------------------- #
# Bucle principal                                                             #
# --------------------------------------------------------------------------- #


async def main() -> None:
    if not WSS:
        log("falta SOLANA_NODE_WSS_ENDPOINT en .env: sin WSS no hay bandera mayhem")
        return

    async with aiohttp.ClientSession() as session:
        vigia = Vigia(session)
        log(
            f"arrancando · {len(vigia.devs)} devs vigilados · "
            f"{len(vigia.vetados)} vetados"
        )
        await avisar(
            session,
            f"👁️ <b>Vigia MAYHEM arrancado</b>\n"
            f"Aviso inmediato de {len(vigia.devs)} devs de tu lista.\n"
            + (
                "Modo ruidoso: avisa de TODAS las mayhem.\n"
                if AVISAR_TODAS
                else f"Del resto, solo si pasan ${UMBRAL_MC:,.0f} "
                f"a los {ESPERA_S:.0f}s.\n"
            )
            + "No compra nada: solo avisa.",
        )

        listener = UniversalLogsListener(WSS, platforms=[Platform.PUMP_FUN])
        escucha = asyncio.create_task(listener.listen_for_tokens(vigia.al_detectar))

        prox = time.time() + RESUMEN_H * 3600
        while True:
            await asyncio.sleep(60)
            if escucha.done():  # el listener reconecta solo; si muere, se ve aqui
                log("el listener murio, lo relanzo")
                escucha = asyncio.create_task(
                    listener.listen_for_tokens(vigia.al_detectar)
                )
            if time.time() >= prox:
                await vigia.resumen()
                prox = time.time() + RESUMEN_H * 3600


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("adios")
