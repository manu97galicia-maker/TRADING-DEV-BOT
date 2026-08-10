"""Construye la lista de los mejores devs de pump.fun POR VOLUMEN DE DISPARO.

El problema de las listas anteriores: se elegian por calidad y luego no
lanzaban nunca (en julio, 103 de 106 no dispararon ni una vez). Aqui cada dev
se mide por dos cosas a la vez:

  - CALIDAD  : % de sus monedas que llegaron al final de la curva (migradas).
  - CADENCIA : cuantas lanza al dia, contadas sobre sus ultimos 7 dias.

Se ordenan por calidad y se van sumando hasta juntar ~50 lanzamientos diarios,
que es lo que pidio Manu.
"""

import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://frontend-api-v3.pump.fun"
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

PAGINAS = 12          # 12 x 50 = 600 creaciones recientes de las que sacar devs
OBJETIVO_DIA = 50     # lanzamientos diarios que se quieren cubrir
MIN_MIGRACIONES = 2   # historial minimo para que el porcentaje signifique algo


def get(url, intentos=5):
    for i in range(intentos):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=H), timeout=30))
        except Exception:
            if i == intentos - 1:
                return None
            time.sleep(1.5 + i * 2)


def recolectar_creadores():
    vistos = {}
    for p in range(PAGINAS):
        d = get(f"{API}/coins?sort=created_timestamp&order=DESC"
                f"&limit=50&offset={p * 50}")
        if not d:
            break
        for c in (d if isinstance(d, list) else []):
            cre = c.get("creator")
            if cre:
                vistos[cre] = vistos.get(cre, 0) + 1
        time.sleep(0.4)
    return vistos


def ficha(cre):
    d = get(f"{API}/coins?creator={cre}&limit=200&offset=0")
    if not d or not isinstance(d, list) or not d:
        return None
    lanz = len(d)
    migs = sum(1 for x in d if x.get("complete") is True)
    ts = sorted((x.get("created_timestamp") or 0) for x in d)
    ahora = time.time() * 1000
    ult7 = [t for t in ts if t > ahora - 7 * 86400_000]
    ult1 = [t for t in ts if t > ahora - 86400_000]
    dias_activo = max(1.0, (ts[-1] - ts[0]) / 86400_000) if len(ts) > 1 else 1.0

    # FRECUENCIA MEDIANA: la mitad de sus lanzamientos van mas seguidos que
    # esto. Se usa la mediana y no la media porque estos devs trabajan a
    # rafagas -- veinte monedas en una hora y luego dos dias parados -- y la
    # media sale de un sitio donde no hay nadie.
    huecos_h = [
        (ts[i + 1] - ts[i]) / 3_600_000 for i in range(len(ts) - 1)
    ] if len(ts) > 1 else []
    hueco_mediano = statistics.median(huecos_h) if huecos_h else None

    # ATH: hasta donde suelen llegar sus monedas. La media la levanta un solo
    # pelotazo, asi que se guardan las dos y manda la mediana.
    aths = [float(x.get("ath_market_cap") or 0) for x in d]
    aths = [a for a in aths if a > 0]
    return {
        "dev": cre,
        "lanzamientos": lanz,
        "migradas": migs,
        "tasa": migs / lanz,
        "dia_7d": len(ult7) / 7.0,
        "ayer": len(ult1),
        "media_historica": lanz / dias_activo,
        "hueco_mediano_h": round(hueco_mediano, 2) if hueco_mediano is not None else None,
        "ath_medio": round(statistics.mean(aths)) if aths else 0,
        "ath_mediano": round(statistics.median(aths)) if aths else 0,
        "ath_max": round(max(aths)) if aths else 0,
        "monedas_con_ath": len(aths),
    }


print("Recolectando creadores de las ultimas creaciones...")
vistos = recolectar_creadores()
print(f"  {len(vistos)} creadores distintos\n")

print("Midiendo historial de cada uno (tarda)...")
fichas = []
with ThreadPoolExecutor(max_workers=5) as ex:
    for i, f in enumerate(ex.map(ficha, list(vistos))):
        if f:
            fichas.append(f)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(vistos)}")

buenos = [
    f for f in fichas
    if f["migradas"] >= MIN_MIGRACIONES and f["dia_7d"] > 0
]
buenos.sort(key=lambda f: (-f["tasa"], -f["dia_7d"]))

print(f"\n{len(fichas)} medidos, {len(buenos)} con {MIN_MIGRACIONES}+ migradas "
      f"y actividad esta semana\n")

print("%-46s %6s %6s %7s %8s %9s %11s %11s" % (
    "dev", "migr", "lanz", "tasa", "lanz/dia", "cada", "ATH medio", "ATH mediano"))
print("-" * 116)
sel, acum = [], 0.0
for f in buenos:
    if acum >= OBJETIVO_DIA:
        break
    sel.append(f)
    acum += f["dia_7d"]
    h = f["hueco_mediano_h"]
    cada = ("%.1f h" % h) if h is not None and h < 48 else (
        ("%.1f d" % (h / 24)) if h is not None else "-")
    print("%-46s %6d %6d %6.1f%% %8.1f %9s %11s %11s" % (
        f["dev"], f["migradas"], f["lanzamientos"], f["tasa"] * 100,
        f["dia_7d"], cada,
        "$%s" % format(f["ath_medio"], ","),
        "$%s" % format(f["ath_mediano"], ",")))

print("-" * 116)
print("%d devs -> %.1f lanzamientos/dia esperados" % (len(sel), acum))
if sel:
    tm = sum(f["migradas"] for f in sel) / sum(f["lanzamientos"] for f in sel)
    print("tasa de migracion media ponderada: %.1f%%" % (tm * 100))
    med = statistics.median([f["ath_mediano"] for f in sel if f["ath_mediano"]])
    print("ATH mediano del grupo            : $%s" % format(round(med), ","))
    hs = [f["hueco_mediano_h"] for f in sel if f["hueco_mediano_h"]]
    if hs:
        print("hueco mediano entre lanzamientos : %.1f h" % statistics.median(hs))

with open("/root/ai-bot/top_devs_cadencia.json", "w", encoding="utf-8") as fh:
    json.dump({
        "generado": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "objetivo_lanzamientos_dia": OBJETIVO_DIA,
        "esperados_dia": round(acum, 1),
        "wallets": [f["dev"] for f in sel],
        "detalle": sel,
    }, fh, indent=1)
print("\nguardado en /root/ai-bot/top_devs_cadencia.json")
