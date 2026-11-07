"""Presion compradora en los DEX: PumpSwap, Raydium y Meteora (Bags incluido).

Pedido el 7-nov: "puedes coger tambien de raydium con mismas condiciones" y
"anade pumpswap y meteora y bags, todos tienen que ser liquidez locked".

DE DONDE SALE LA PRESION. Escuchar en directo los programas de Raydium,
PumpSwap y Meteora seria otra suscripcion a Helius por cada uno, con mucho mas
trafico que pump.fun, y para saber QUIEN compra habria que pedir cada
transaccion aparte: no cabe en el droplet ni en el plan. GeckoTerminal ya lo
cuenta gratis para cada pool: compras, ventas, COMPRADORES DISTINTOS y volumen
de los ultimos 5 minutos. Asi que esto pregunta cada `intervalo_seg` por los
pools con mas movimiento y los que acaban de nacer, y se queda con los de los
DEX permitidos. La ventana es de 5 minutos, no de 5 segundos: es lo que da la
fuente.

LIBRE DE RUGS. A cada candidata que pasa la presion se le pide el informe de
RugCheck, y se descarta si: no hay informe, la marca como rug, la liquidez
bloqueada o quemada es menor que `min_lp_bloqueada_pct`, o tiene activo un
riesgo de los que dejan robar (congelar carteras, crear mas monedas, un
titular con casi todo).

EN PAPEL. El modo papel del bot lee el precio de la curva de pump.fun, y una
moneda de un DEX no la tiene. Asi que esta fuente trae su propia simulacion:
"compra" al precio del DEX, sigue el precio (DexScreener) y aplica la MISMA
salida que el bot (TP por tramos, stop, trailing, suelo tras el TP1, venta a
los N segundos si va en rojo). Cada cierre va al log y a
`logs/dex_presion_papel.jsonl`, con el resultado bruto y el neto estimado con
las comisiones de prioridad. En papel no se firma nada.

EN REAL se le pasa la moneda al trader, que compra y vende por Jupiter. Ese
camino no se ha probado aun contra la cadena desde aqui: por eso arranca en
papel.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from dexscreener.rugcheck import RugCheckClient, RugReport
from utils.logger import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from interfaces.core import TokenInfo

logger = get_logger(__name__)

GECKO = "https://api.geckoterminal.com/api/v2/networks/solana"
DEXSCREENER_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/{}"
#: Monedas que hacen de "dinero" en un pool: la que se compra es la otra.
COTIZACION = {
    "So11111111111111111111111111111111111111112",  # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
#: Riesgos de RugCheck que dejan a quien lanzo llevarse el dinero.
RIESGOS_PROHIBIDOS = ("freeze", "mint authority", "single holder", "top 10", "rugged")
CABECERAS = {"Accept": "application/json", "User-Agent": "pump-bot"}
PAPEL_JSONL = Path("logs/dex_presion_papel.jsonl")
#: Por debajo de esto la posicion simulada se da por vendida entera.
_NADA = 1e-9


# --------------------------------------------------------------- lectura pura
@dataclass
class Pool:
    """Lo que interesa de un pool de GeckoTerminal."""

    mint: str
    pool: str
    dex: str
    nombre: str = ""
    compras: int = 0
    ventas: int = 0
    compradores: int = 0
    volumen_usd: float = 0.0
    liquidez_usd: float = 0.0
    precio_usd: float = 0.0
    #: Segundos desde que se creo el pool, o None si GeckoTerminal no lo dice.
    edad_seg: float | None = None


def _id(rel: dict, clave: str) -> str:
    d = ((rel or {}).get(clave) or {}).get("data") or {}
    return str(d.get("id") or "")


def _num(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def lee_pools(payload: object) -> list[Pool]:
    """Los pools de una respuesta de GeckoTerminal, con la moneda que se compra."""
    fuera: list[Pool] = []
    for d in (payload or {}).get("data") or [] if isinstance(payload, dict) else []:
        a, rel = d.get("attributes") or {}, d.get("relationships") or {}
        base = _id(rel, "base_token").removeprefix("solana_")
        cotiza = _id(rel, "quote_token").removeprefix("solana_")
        mint = cotiza if base in COTIZACION else base
        if not mint or mint in COTIZACION:
            continue
        m5 = ((a.get("transactions") or {}).get("m5")) or {}
        try:
            # El droplet corre Python 3.10: su fromisoformat no entiende la
            # "Z" final con la que GeckoTerminal manda las fechas.
            texto = str(a.get("pool_created_at")).replace("Z", "+00:00")
            creado = datetime.fromisoformat(texto)
            edad = time.time() - creado.timestamp()
        except (TypeError, ValueError):
            edad = None
        fuera.append(
            Pool(
                mint=mint,
                pool=str(a.get("address") or ""),
                dex=_id(rel, "dex"),
                nombre=str(a.get("name") or ""),
                compras=int(_num(m5.get("buys"))),
                ventas=int(_num(m5.get("sells"))),
                compradores=int(_num(m5.get("buyers"))),
                volumen_usd=_num((a.get("volume_usd") or {}).get("m5")),
                liquidez_usd=_num(a.get("reserve_in_usd")),
                precio_usd=_num(a.get("base_token_price_usd")) if base == mint else 0.0,
                edad_seg=edad,
            )
        )
    return fuera


def dex_permitido(dex: str, prefijos: list[str]) -> bool:
    d = dex.lower()
    return any(d.startswith(p.lower()) for p in prefijos)


def presion_ok(p: Pool, cfg: dict) -> tuple[bool, str]:
    """La presion de 5 minutos que pide la config. Devuelve (ok, motivo)."""
    ratio = p.compras / p.ventas if p.ventas else float(p.compras > 0) * 99.0
    faltas = []
    if p.compradores < int(cfg.get("min_compradores_5m", 0)):
        faltas.append(f"{p.compradores} compradores")
    if p.compras < int(cfg.get("min_compras_5m", 0)):
        faltas.append(f"{p.compras} compras")
    if ratio < float(cfg.get("min_ratio_5m", 0)):
        faltas.append(f"ratio {ratio:.2f}x")
    if p.volumen_usd < float(cfg.get("min_volumen_5m_usd", 0)):
        faltas.append(f"volumen {p.volumen_usd:,.0f} $")
    if p.liquidez_usd < float(cfg.get("min_liquidez_usd", 0)):
        faltas.append(f"liquidez {p.liquidez_usd:,.0f} $")
    # 7-nov: "no compres monedas con menos de 10 minutos de vida".
    min_edad = float(cfg.get("min_edad_min", 0)) * 60
    if min_edad > 0 and (p.edad_seg is None or p.edad_seg < min_edad):
        faltas.append(
            "sin edad" if p.edad_seg is None else f"{p.edad_seg / 60:.1f} min de vida"
        )
    return (
        not faltas,
        ", ".join(faltas) or f"{p.compradores} compradores, {ratio:.1f}x",
    )


def rug_ok(r: RugReport | None, min_lp: float) -> tuple[bool, str]:
    """Libre de rugs segun RugCheck. Ante la duda, NO."""
    if r is None or not r.found:
        return False, "sin informe de RugCheck"
    if r.rugged:
        return False, "RugCheck la marca como rug"
    if r.lp_locked_pct < min_lp:
        return False, f"liquidez bloqueada {r.lp_locked_pct:.0f}% < {min_lp:.0f}%"
    for nombre, nivel in r.risks:
        n = str(nombre).lower()
        if str(nivel).lower() == "danger" and any(x in n for x in RIESGOS_PROHIBIDOS):
            return False, f"riesgo: {nombre}"
    return True, f"liquidez bloqueada {r.lp_locked_pct:.0f}%"


@dataclass
class Simulacion:
    """La salida del bot, aplicada a una serie de precios. Sin firmar nada.

    `tramos` son (subida, parte de la posicion ORIGINAL), igual que en el
    trader. Orden por precio: stop (antes del TP1), suelo (despues del TP1),
    trailing, tramos y moneda muerta.
    """

    entrada: float
    tramos: list[tuple[float, float]]
    stop: float
    trailing: float
    margen_tras_tp: float
    muerta_seg: float
    muerta_gain: float
    t0: float = 0.0
    pico: float = 0.0
    queda: float = 1.0
    hechos: int = 0
    ventas: list[tuple[str, float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.pico = self.entrada
        self.tramos = sorted(self.tramos)

    @property
    def cerrada(self) -> bool:
        return self.queda <= _NADA

    def _vende(self, motivo: str, parte: float, precio: float) -> None:
        parte = min(parte, self.queda)
        if parte > 0:
            self.ventas.append((motivo, parte, precio))
            self.queda -= parte

    def paso(self, precio: float, t: float) -> None:
        if self.cerrada or precio <= 0:
            return
        e = self.entrada
        self.pico = max(self.pico, precio)
        if self.hechos == 0 and self.stop > 0 and precio <= e * (1 - self.stop):
            self._vende("stop_loss", self.queda, precio)
            return
        if self.hechos > 0 and precio <= e * (1 + self.margen_tras_tp):
            self._vende("suelo_tras_tp", self.queda, precio)
            return
        if (
            self.trailing > 0
            and self.pico >= e * 1.2
            and precio <= self.pico * (1 - self.trailing)
        ):
            self._vende("trailing_stop", self.queda, precio)
            return
        while self.hechos < len(self.tramos):
            subida, parte = self.tramos[self.hechos]
            if precio < e * (1 + subida):
                break
            self.hechos += 1
            self._vende(f"tp{self.hechos}_+{subida * 100:.0f}%", parte, precio)
        if (
            self.hechos == 0
            and self.muerta_seg > 0
            and t - self.t0 >= self.muerta_seg
            and precio < e * (1 + self.muerta_gain)
        ):
            self._vende("moneda_muerta", self.queda, precio)

    def resultado(self) -> float:
        """Ganancia bruta de lo vendido, en tanto por uno sobre lo invertido."""
        return sum(p * pr / self.entrada for _m, p, pr in self.ventas) - (
            1 - self.queda
        )


# ------------------------------------------------------------------- fuente
class DexPresionSource:
    """Busca presion en los pools de los DEX permitidos y compra (o simula)."""

    def __init__(
        self,
        client: object,
        platform_impls: object,
        cfg: dict,
        salida: dict,
        *,
        en_papel: bool,
    ) -> None:
        self._client = client
        self._impls = platform_impls
        self.cfg = dict(cfg or {})
        self.salida = dict(salida or {})
        self.en_papel = bool(en_papel)
        self.prefijos = list(self.cfg.get("dex") or ["raydium", "pumpswap", "meteora"])
        # 7-nov, Manu: "no compres mas que no sea pumpfun o pumpswap". Solo
        # monedas nacidas en pump.fun (su mint acaba en "pump"): en PumpSwap
        # tambien se negocian monedas de fuera, y esas no entran.
        self.solo_pumpfun = bool(self.cfg.get("solo_pumpfun", False))
        self.intervalo = float(self.cfg.get("intervalo_seg", 20))
        self.min_lp = float(self.cfg.get("min_lp_bloqueada_pct", 90))
        self.max_papel = int(self.cfg.get("max_posiciones_papel", 4))
        self._vistas: dict[str, float] = {}
        self._dex_desconocidos: set[str] = set()
        self._abiertas: dict[str, Simulacion] = {}
        self._cerradas: list[float] = []

    async def _get(self, s: aiohttp.ClientSession, url: str) -> object:
        try:
            async with s.get(url, headers=CABECERAS) as r:
                if r.status != 200:  # noqa: PLR2004
                    logger.info(
                        "DEX presion: %s respondio %s", url.split("?")[0], r.status
                    )
                    return None
                return await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError, ValueError):
            return None

    async def _candidatas(self, s: aiohttp.ClientSession) -> list[Pool]:
        pools: list[Pool] = []
        for ruta in (
            "/trending_pools?duration=5m&page=1",
            "/new_pools?page=1",
            "/trending_pools?duration=5m&page=2",
        ):
            pools += lee_pools(await self._get(s, GECKO + ruta))
        fuera, vistos = [], set()
        for p in pools:
            if p.mint in vistos:
                continue
            vistos.add(p.mint)
            if self.solo_pumpfun and not p.mint.endswith("pump"):
                continue
            if dex_permitido(p.dex, self.prefijos):
                fuera.append(p)
            elif p.dex and p.dex not in self._dex_desconocidos:
                self._dex_desconocidos.add(p.dex)
                logger.info("DEX presion: '%s' no esta en la lista; se ignora", p.dex)
        return fuera

    def _repetida(self, mint: str) -> bool:
        """Una moneda se mira como mucho una vez por hora."""
        ahora = time.monotonic()
        if ahora - self._vistas.get(mint, -1e18) < 3600:  # noqa: PLR2004
            return True
        self._vistas[mint] = ahora
        return False

    async def _evalua(
        self,
        p: Pool,
        rug: RugCheckClient,
        token_callback: Callable[[TokenInfo], Awaitable[None]],
    ) -> None:
        ok, motivo = presion_ok(p, self.cfg)
        if not ok:
            return
        if self._repetida(p.mint):
            return
        informe = None
        try:
            informe = await rug.get_report(p.mint)
        except Exception:  # noqa: BLE001
            informe = None
        limpio, porque = rug_ok(informe, self.min_lp)
        if not limpio:
            logger.info("Evitado %s (%s, %s): %s", p.nombre, p.mint, p.dex, porque)
            return
        logger.info(
            "PRESION DEX %s (%s, %s): %s · %s · liquidez %s $",
            p.nombre,
            p.mint,
            p.dex,
            motivo,
            porque,
            f"{p.liquidez_usd:,.0f}",
        )
        if self.en_papel:
            self._abre_papel(p)
        else:
            await self._compra_real(p, token_callback)

    def _abre_papel(self, p: Pool) -> None:
        if len(self._abiertas) >= self.max_papel or p.mint in self._abiertas:
            logger.info(
                "PAPEL DEX: %s no se simula (ya hay %d)", p.mint, len(self._abiertas)
            )
            return
        if p.precio_usd <= 0:
            return
        s = self.salida
        self._abiertas[p.mint] = Simulacion(
            entrada=p.precio_usd,
            tramos=list(s.get("tramos") or []),
            stop=float(s.get("stop") or 0),
            trailing=float(s.get("trailing") or 0),
            margen_tras_tp=float(s.get("margen_tras_tp") or 0),
            muerta_seg=float(s.get("muerta_seg") or 0),
            muerta_gain=float(s.get("muerta_gain") or 0),
            t0=time.monotonic(),
        )
        logger.info("PAPEL DEX COMPRA %s (%s) a %.10g $", p.nombre, p.dex, p.precio_usd)

    async def _sigue_papel(self, s: aiohttp.ClientSession) -> None:
        if not self._abiertas:
            return
        mints = list(self._abiertas)[:30]
        datos = await self._get(s, DEXSCREENER_TOKENS.format(",".join(mints)))
        precios: dict[str, tuple[float, float]] = {}
        for par in datos if isinstance(datos, list) else []:
            m = ((par.get("baseToken") or {}).get("address")) or ""
            liq = _num((par.get("liquidity") or {}).get("usd"))
            if m in self._abiertas and liq >= precios.get(m, (0, -1))[1]:
                precios[m] = (_num(par.get("priceUsd")), liq)
        ahora = time.monotonic()
        for m, (precio, _liq) in precios.items():
            sim = self._abiertas[m]
            sim.paso(precio, ahora)
            if sim.cerrada:
                self._cierra_papel(m, sim)

    def _cierra_papel(self, mint: str, sim: Simulacion) -> None:
        del self._abiertas[mint]
        bruto = sim.resultado()
        importe = float(self.cfg.get("importe_papel_sol", 0.15))
        comisiones = float(self.cfg.get("prioridad_compra_sol", 0.007)) + len(
            sim.ventas
        ) * float(self.cfg.get("prioridad_venta_sol", 0.006))
        neto = bruto - comisiones / importe if importe > 0 else bruto
        self._cerradas.append(neto)
        logger.info(
            "PAPEL DEX CIERRA %s: bruto %+.1f%%, neto estimado %+.1f%% (%s)",
            mint,
            bruto * 100,
            neto * 100,
            ", ".join(f"{m} {pr / sim.entrada - 1:+.0%}" for m, _p, pr in sim.ventas),
        )
        try:
            PAPEL_JSONL.parent.mkdir(parents=True, exist_ok=True)
            with PAPEL_JSONL.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "mint": mint,
                            "ts": time.time(),
                            "bruto": bruto,
                            "neto": neto,
                            "ventas": sim.ventas,
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass

    async def _compra_real(
        self, p: Pool, token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        from trading.token_builder import (  # noqa: PLC0415
            build_generic_token_info,
            build_pumpfun_token_info,
        )

        if p.mint.endswith("pump"):
            ti = await build_pumpfun_token_info(
                p.mint, self._impls, self._client, permitir_migradas=True
            )
        else:
            ti = await build_generic_token_info(p.mint, self._client)
        if ti is None:
            return
        ti.additional_data = {
            **(ti.additional_data or {}),
            "migrated": True,
            "origen": "dex_presion",
            "acquisition_source": "dex_presion",
            "dex": p.dex,
        }
        await token_callback(ti)

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        logger.info(
            "DEX presion %s: %s cada %.0f s; LP bloqueada >= %.0f%%",
            "EN PAPEL" if self.en_papel else "EN REAL",
            ", ".join(self.prefijos),
            self.intervalo,
            self.min_lp,
        )
        tiempo = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=tiempo) as s, RugCheckClient() as rug:
            ultimo = 0.0
            while True:
                if time.monotonic() - ultimo >= self.intervalo:
                    ultimo = time.monotonic()
                    for p in await self._candidatas(s):
                        await self._evalua(p, rug, token_callback)
                await self._sigue_papel(s)
                await asyncio.sleep(5)
