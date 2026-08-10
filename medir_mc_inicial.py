"""Market cap con el que arrancan las monedas de cada dev de la lista.

Manu lo pidio "a los 5 segundos". GMGN no da velas de menos de 30 s, asi que se
miden dos cosas de la PRIMERA vela, que es lo mas cerca que se puede estar:

  MC apertura : precio de apertura de la primera vela = el arranque. Es lo que
                mas se parece a "a los 5 segundos".
  MC a 30s    : cierre de esa misma vela.

La diferencia entre los dos dice lo rapido que se mueve ese dev en el primer
medio minuto, que es justo la ventana en la que compra el bot.
"""

import json
import statistics
import subprocess
import time

TOTAL_SUPPLY = 1_000_000_000
POR_DEV = 4  # monedas recientes que se miran de cada dev
API = "https://frontend-api-v3.pump.fun"


def api(url):
    import urllib.request
    H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    for i in range(4):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=H), timeout=25))
        except Exception:
            if i == 3:
                return None
            time.sleep(2)


def velas(addr):
    try:
        out = subprocess.run(
            ["gmgn-cli", "market", "kline", "--chain", "sol",
             "--address", addr, "--resolution", "30s", "--raw"],
            capture_output=True, text=True, timeout=90, shell=True,
        ).stdout
        return json.loads(out[out.index("{"):])["list"]
    except Exception:
        return None


devs = json.load(open("top_devs_cadencia.json", encoding="utf-8"))["detalle"]
print("Midiendo %d devs x %d monedas...\n" % (len(devs), POR_DEV))

print("%-46s %13s %13s %8s" % ("dev", "MC apertura", "MC a 30 s", "muestra"))
print("-" * 86)

todos_ap, todos_30 = [], []
for d in devs:
    monedas = api(f"{API}/coins?creator={d['dev']}&limit={POR_DEV}&offset=0") or []
    aps, c30 = [], []
    for c in (monedas if isinstance(monedas, list) else [])[:POR_DEV]:
        vs = velas(c.get("mint", ""))
        if not vs:
            continue
        aps.append(float(vs[0]["open"]) * TOTAL_SUPPLY)
        c30.append(float(vs[0]["close"]) * TOTAL_SUPPLY)
    if not aps:
        print("%-46s %13s %13s %8d" % (d["dev"], "-", "-", 0))
        continue
    ma, m3 = statistics.mean(aps), statistics.mean(c30)
    d["mc_apertura_medio"] = round(ma)
    d["mc_30s_medio"] = round(m3)
    d["muestra_mc"] = len(aps)
    todos_ap.extend(aps)
    todos_30.extend(c30)
    print("%-46s %13s %13s %8d" % (
        d["dev"], "$%s" % format(round(ma), ","), "$%s" % format(round(m3), ","), len(aps)))

print("-" * 86)
if todos_ap:
    print("MEDIA del grupo   apertura $%s   a 30 s $%s" % (
        format(round(statistics.mean(todos_ap)), ","),
        format(round(statistics.mean(todos_30)), ",")))
    print("MEDIANA del grupo apertura $%s   a 30 s $%s" % (
        format(round(statistics.median(todos_ap)), ","),
        format(round(statistics.median(todos_30)), ",")))

datos = json.load(open("top_devs_cadencia.json", encoding="utf-8"))
datos["detalle"] = devs
datos["nota_mc"] = ("MC de apertura y a 30 s de la primera vela. GMGN no da "
                    "resolucion menor de 30 s, asi que no hay dato exacto a 5 s.")
json.dump(datos, open("top_devs_cadencia.json", "w", encoding="utf-8"), indent=1)
print("\nactualizado top_devs_cadencia.json")
