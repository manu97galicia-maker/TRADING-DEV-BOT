"""Simula en Telegram el ciclo completo de una posicion, sin operar ni gastar.

Recorre dos casos con las MISMAS funciones que usa el bot -- entrada, cada
peldano de la escalera segun salta, y el informe final -- para poder revisar
como se ven los avisos sin esperar a que llegue una senal de verdad.

La escalera y el stop se leen del YAML de la estrategia, asi que esto refleja
la configuracion real: si cambias los TP, este simulador cambia con ellos.

    uv run learning-examples/simular_avisos_telegram.py            # solo imprime
    uv run learning-examples/simular_avisos_telegram.py --enviar   # manda a Telegram

Necesita TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el .env para --enviar.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from monitoring.notifier import (
    build_ladder_event_notification,
    build_paper_entry_notification,
    build_paper_result_notification,
    notify,
)
from trading.tiered_exit import Tier, build_tiers, ladder_progress

CONFIG = Path("bots") / "bot-paper-ai-entry.yaml"
MINT = "SiMuLaC10NnoEsUnaMonedaRealSoloPruebapump"
NAME = "Simulacion Bot"
SYMBOL = "SIMUL"
ENTRY = 0.000000123456
CURVE_SOL = 42.0
MC_USD = 12_500.0

# De donde vino la senal, para que se vea el motivo en cada aviso.
SIGNAL = {
    "acquisition_source": "dexscreener",
    "dexscreener_boosts": 750,
    "signal_buy_amount": 0.20,
}


def cargar_escalera() -> tuple[list[Tier], float, float]:
    """Devuelve (peldanos, stop_loss_fraccion, importe) leidos del YAML real."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    trade = cfg["trade"]
    pares = [
        (trade.get("take_profit_percentage"), trade.get("take_profit_sell_percentage")),
        *((trade.get(f"tp{n}_gain"), trade.get(f"tp{n}_sell")) for n in range(2, 6)),
    ]
    # El importe es el del escalon que corresponde a la senal simulada (un
    # boost de 750 va por boost_buy_amount), no el buy_amount generico: si no,
    # el aviso mostraria un tamano que el bot nunca usaria para esa senal.
    dexpaid = cfg.get("filters", {}).get("buy_on_dex_paid", {})
    importe = float(dexpaid.get("boost_buy_amount") or trade.get("buy_amount") or 0.15)
    return (
        build_tiers(pares),
        float(trade.get("stop_loss_percentage") or 0),
        importe,
    )


def emitir(mensaje: str, *, enviar: bool) -> None:
    """Imprime el aviso y, si se pidio, lo manda de verdad a Telegram."""
    print("\n" + "-" * 60)
    print(mensaje)
    if enviar:
        notify(mensaje)


def _registro(  # noqa: PLR0913
    tiers: list[Tier],
    executed: list[bool],
    ventas: list[dict],
    importe: float,
    sl_ini: float,
    sl_fin: float,
    ultimo: float,
    roi: float,
    motivo: str,
    pico: float,
    minimo: float,
) -> dict:
    """Arma el registro que espera el informe final."""
    # Cuenta TODAS las ventas, tambien la del stop: ladder_progress solo sabe
    # de peldanos de TP y dejaria el informe diciendo "restante 10%" cuando el
    # stop ya habia cerrado la posicion entera.
    vendido = min(1.0, sum(v["position_pct"] for v in ventas) / 100)
    valor = importe * (1 + roi)
    return {
        "mint": MINT,
        "symbol": SYMBOL,
        "name": NAME,
        "amount_sol": importe,
        "curve_sol": CURVE_SOL,
        "entry_price_sol": ENTRY,
        "last_price_sol": ultimo,
        "peak_pct": round((pico / ENTRY - 1) * 100, 1),
        "trough_pct": round((minimo / ENTRY - 1) * 100, 1),
        "initial_stop_price_sol": sl_ini,
        "final_stop_price_sol": sl_fin,
        "take_profit_tiers": [
            {
                "reason": f"TP{i + 1}",
                "gain_pct": round(t.gain * 100, 2),
                "sell_pct": round(t.sell * 100, 2),
                "price_sol": ENTRY * (1 + t.gain),
                "hit": executed[i],
            }
            for i, t in enumerate(tiers)
        ],
        "sales": ventas,
        "average_sale_price_sol": (
            sum(v["price_sol"] * v["position_pct"] / 100 for v in ventas) / vendido
            if vendido > 0
            else 0.0
        ),
        "exit_reason": motivo,
        "strategy_closed": True,
        "position_sold_pct": round(vendido * 100, 1),
        "position_remaining_pct": round((1 - vendido) * 100, 1),
        "tp_hits": sum(executed),
        "roi_pct": round(roi * 100, 2),
        "value_sol": valor,
        "pnl_sol": valor - importe,
        "win": valor > importe,
    }


def caso_ganadora(
    tiers: list[Tier], sl_pct: float, importe: float, *, enviar: bool
) -> None:
    """Toca TP1, TP2 y TP3, y luego cae hasta el stop ya protegido en TP2."""
    print("\n" + "=" * 60)
    print("CASO 1 - GANADORA: sube, cobra tres peldanos y cae al stop protegido")
    print("=" * 60)

    executed = [False] * len(tiers)
    sale_prices: list[float | None] = [None] * len(tiers)
    ventas: list[dict] = []
    sl_ini = ENTRY * (1 - sl_pct)
    suelo = sl_ini

    emitir(
        build_paper_entry_notification(
            symbol=SYMBOL,
            mint=MINT,
            name=NAME,
            amount_sol=importe,
            source_data=SIGNAL,
            entry_price_sol=ENTRY,
            market_cap_usd=MC_USD,
            curve_sol=CURVE_SOL,
            strategy=" · ".join(
                f"+{t.gain * 100:.0f}%/{t.sell * 100:.0f}%" for t in tiers
            ),
            stop_loss_pct=sl_pct * 100,
            take_profit_tiers=[(t.gain, t.sell) for t in tiers],
        ),
        enviar=enviar,
    )

    for i in range(min(3, len(tiers))):
        precio = ENTRY * (1 + tiers[i].gain)
        executed[i] = True
        sale_prices[i] = precio
        ventas.append(
            {
                "reason": f"TP{i + 1}",
                "position_pct": tiers[i].sell * 100,
                "price_sol": precio,
            }
        )
        suelo = ENTRY if i == 0 else ENTRY * (1 + tiers[i - 1].gain)
        _v, _r, total = ladder_progress(ENTRY, precio, tiers, executed, sale_prices)
        emitir(
            build_ladder_event_notification(
                f"TP{i + 1}",
                NAME,
                SYMBOL,
                MINT,
                SIGNAL,
                precio,
                ENTRY,
                importe,
                tiers[i].sell * 100,
                max(
                    0.0,
                    100 - sum(t.sell for j, t in enumerate(tiers) if executed[j]) * 100,
                ),
                total,
                new_stop_price_sol=suelo,
            ),
            enviar=enviar,
        )

    # Se desploma: el stop protegido cierra lo que queda, ya en beneficio.
    vendido, realizado, _t = ladder_progress(ENTRY, suelo, tiers, executed, sale_prices)
    restante = 1 - vendido
    roi = realizado + restante * (suelo / ENTRY - 1)
    ventas.append(
        {"reason": "STOP", "position_pct": round(restante * 100, 1), "price_sol": suelo}
    )
    emitir(
        build_ladder_event_notification(
            "STOP",
            NAME,
            SYMBOL,
            MINT,
            SIGNAL,
            suelo,
            ENTRY,
            importe,
            0.0,
            restante * 100,
            roi,
        ),
        enviar=enviar,
    )
    emitir(
        build_paper_result_notification(
            _registro(
                tiers,
                executed,
                ventas,
                importe,
                sl_ini,
                suelo,
                suelo,
                roi,
                "stop",
                pico=ENTRY * (1 + tiers[min(2, len(tiers) - 1)].gain),
                minimo=suelo,
            ),
            SIGNAL,
            {"winrate": 61.5, "wins": 8, "losses": 5, "n": 13, "pnl": 0.1842},
        ),
        enviar=enviar,
    )


def caso_perdedora(
    tiers: list[Tier], sl_pct: float, importe: float, *, enviar: bool
) -> None:
    """No toca ningun TP: cae directa al stop inicial."""
    print("\n" + "=" * 60)
    print("CASO 2 - PERDEDORA: cae sin tocar ningun TP")
    print("=" * 60)

    executed = [False] * len(tiers)
    sl = ENTRY * (1 - sl_pct)
    roi = -sl_pct

    emitir(
        build_paper_entry_notification(
            symbol=SYMBOL,
            mint=MINT,
            name=NAME,
            amount_sol=importe,
            source_data=SIGNAL,
            entry_price_sol=ENTRY,
            market_cap_usd=MC_USD,
            curve_sol=CURVE_SOL,
            strategy=" · ".join(
                f"+{t.gain * 100:.0f}%/{t.sell * 100:.0f}%" for t in tiers
            ),
            stop_loss_pct=sl_pct * 100,
            take_profit_tiers=[(t.gain, t.sell) for t in tiers],
        ),
        enviar=enviar,
    )
    emitir(
        build_ladder_event_notification(
            "STOP",
            NAME,
            SYMBOL,
            MINT,
            SIGNAL,
            sl,
            ENTRY,
            importe,
            0.0,
            100.0,
            roi,
        ),
        enviar=enviar,
    )
    ventas = [{"reason": "STOP", "position_pct": 100.0, "price_sol": sl}]
    emitir(
        build_paper_result_notification(
            _registro(
                tiers,
                executed,
                ventas,
                importe,
                sl,
                sl,
                sl,
                roi,
                "stop",
                pico=ENTRY,
                minimo=sl,
            ),
            SIGNAL,
            {"winrate": 57.1, "wins": 8, "losses": 6, "n": 14, "pnl": 0.1482},
        ),
        enviar=enviar,
    )


def main() -> None:
    """Recorre los dos casos, imprimiendo y opcionalmente enviando."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    enviar = "--enviar" in sys.argv
    if enviar and not (
        os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")
    ):
        print("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el .env")
        return

    tiers, sl_pct, importe = cargar_escalera()
    print(f"Escalera leida de {CONFIG}:")
    for i, t in enumerate(tiers, 1):
        print(f"  TP{i}: +{t.gain * 100:.0f}% vendiendo {t.sell * 100:.0f}%")
    print(f"  SL: -{sl_pct * 100:.0f}%   ·   importe {importe} SOL")
    if not enviar:
        print("\n(solo impresion -- anade --enviar para mandarlo a Telegram)")

    caso_ganadora(tiers, sl_pct, importe, enviar=enviar)
    caso_perdedora(tiers, sl_pct, importe, enviar=enviar)

    if enviar:
        # notify() manda en un hilo aparte; sin esto el proceso muere antes.
        time.sleep(5)
        print("\nEnviado a Telegram.")


if __name__ == "__main__":
    main()
