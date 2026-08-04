"""Cuentas reales de la cartera, separando lo que hizo CADA bot.

Ahora mismo hay dos bots operando el mismo monedero: el nuestro (local) y el
que quedo corriendo en el droplet desde julio. Sumar sus resultados no dice
nada -- si el conjunto pierde, no se sabe cual de los dos pierde.

La separacion se hace por firma: las transacciones que aparecen en nuestros
logs son nuestras, el resto son del otro. No hay ambiguedad posible, porque una
firma es unica.

Se lee la cadena, no la contabilidad interna del bot: el 1-ago la contabilidad
interna decia una cosa y la cartera otra, y la cartera es la que manda.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

D = Path(__file__).parent

for linea in (D / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    linea = linea.strip()
    if linea and not linea.startswith("#") and "=" in linea:
        k, v = linea.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

RPC = os.environ["SOLANA_NODE_RPC_ENDPOINT"]


def rpc(metodo: str, params: list) -> dict:
    """Consulta con freno y reintentos.

    Este script comparte endpoint con el bot, que va a su ritmo y no se entera
    de que alguien mas esta preguntando. Sin pausa entre llamadas, Helius
    devuelve 429 y ademas se le roba cuota AL BOT, que la necesita para leer
    curvas y vender. Preferimos tardar mas aqui.
    """
    cuerpo = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params}
    ).encode()
    for intento in range(6):
        try:
            peticion = urllib.request.Request(  # noqa: S310
                RPC, data=cuerpo, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(peticion, timeout=30) as r:  # noqa: S310
                datos = json.load(r)
            time.sleep(0.35)
            return datos
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(1.5 * (intento + 1))
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
    return {}


def firmas_nuestras() -> dict[str, str]:
    """Firma -> simbolo, sacado de nuestros propios logs."""
    vistas: dict[str, str] = {}
    for ruta in glob.glob("/tmp/*.log") + glob.glob(str(D / "logs" / "*.log")):
        try:
            texto = Path(ruta).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(
            r"(Buy|Sell) transaction confirmed: ([1-9A-HJ-NP-Za-km-z]{60,})", texto
        ):
            vistas[m.group(2)] = m.group(1)
    return vistas


def main() -> None:
    import base58
    from solders.keypair import Keypair

    kp = Keypair.from_bytes(base58.b58decode(os.environ["SOLANA_PRIVATE_KEY"]))
    pk = str(kp.pubkey())

    nuestras = firmas_nuestras()
    print(f"firmas reconocidas en nuestros logs: {len(nuestras)}\n")

    sigs = rpc("getSignaturesForAddress", [pk, {"limit": 30}]).get("result") or []

    grupos: dict[str, list[float]] = {"NUESTRO": [], "OTRO (droplet)": []}
    filas = []
    for s in sigs:
        firma = s["signature"]
        det = rpc(
            "getTransaction",
            [firma, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        ).get("result")
        if not det:
            continue
        meta = det["meta"]
        claves = [k["pubkey"] for k in det["transaction"]["message"]["accountKeys"]]
        if pk not in claves:
            continue
        i = claves.index(pk)
        delta = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
        quien = "NUESTRO" if firma in nuestras else "OTRO (droplet)"
        # Un ingreso desde fuera no es una operacion: distorsiona el total.
        if delta > 0.4:
            quien = "INGRESO"
        else:
            grupos[quien].append(delta)
        hora = (
            datetime.fromtimestamp(s["blockTime"]).strftime("%H:%M:%S")
            if s.get("blockTime")
            else "?"
        )
        filas.append((hora, quien, delta, firma[:12]))

    print(f"{'hora':10} {'quien':16} {'variacion':>12}")
    print("-" * 44)
    for hora, quien, delta, _f in reversed(filas):
        print(f"  {hora:10} {quien:16} {delta:>+11.6f}")

    print(f"\n{'=' * 44}\nRESULTADO POR BOT\n{'=' * 44}")
    for nombre, vals in grupos.items():
        if not vals:
            print(f"  {nombre:16} sin operaciones")
            continue
        print(
            f"  {nombre:16} {len(vals):>3} transacciones  "
            f"{sum(vals):>+10.6f} SOL"
        )

    saldo = (
        rpc("getBalance", [pk, {"commitment": "processed"}])["result"]["value"] / 1e9
    )
    print(f"\n  saldo actual: {saldo:.6f} SOL")


if __name__ == "__main__":
    main()
