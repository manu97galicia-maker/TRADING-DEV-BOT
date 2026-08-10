"""Busca el DINERO LISTO: wallets que compran primero en las monedas que luego corren.

Las listas que ya hay en el repo (top_devs_*) miran al que LANZA. Esta mira al
que COMPRA. La idea es que si una wallet aparece entre los primeros compradores
de cinco monedas que despues hicieron $1M, no es suerte: sabe algo, y copiarla
es una senal mas temprana que cualquier filtro de volumen.

COMO FUNCIONA
  1. Saca monedas migradas recientes de la API de pump.fun.
  2. De cada una lee su historia de firmas en la cadena desde el principio y se
     queda con las primeras wallets que compran de verdad.
  3. Hace lo MISMO con una muestra de PERDEDORAS. Sin ese control el resultado
     no vale nada: los bots que snipean todo salen tambien en las ganadoras y
     pareceria que aciertan.
  4. Puntua = en cuantas ganadoras sale, castigando a quien sale en las perdedoras.

DECISIONES QUE NO SON OBVIAS

  - GANADORA no es "ATH alto" a secas. El campo `ath_market_cap` de la API se
    calcula como precio_maximo x 1e9 y sale disparado en cuanto alguien mueve
    un pool vacio: se han visto valores de 1e23. Ademas de acotarlo se exige
    que el pool tenga WSOL de verdad ahora mismo. Una moneda con $90M de ATH y
    1,3 WSOL en el pool no es una ganadora, es un cadaver con un numero bonito.

  - El COMPRADOR es el que FIRMA Y PAGA la transaccion y ademas ve subir su
    propio saldo del token. No vale con "a alguien le subio el saldo": los devs
    reparten tokens a decenas de wallets desde su propia transaccion para
    fabricar holders, y esos receptores no han comprado nada.

  - Solo cuentan las transacciones que NO fallaron. En los primeros segundos de
    una moneda hay cientos de intentos fallidos de snipers; contarlos seria
    contar intenciones, no dinero.

  - Se leen las firmas del MINT y no de la curva. Desde el cambio de pump.fun la
    mayoria de lanzamientos hacen create + compra del dev + migracion en el
    mismo bloque, asi que la curva no llega a operar y todo el mercado ocurre en
    el pool de PumpSwap. El mint es el unico sitio donde estan las dos cosas.

  - Las PERDEDORAS del control tambien tienen que estar MIGRADAS. La primera
    version compraba monedas muertas sin migrar y salia que los snipers grandes
    tenian un 0% de fallos, lo cual era mentira: es que esos bots ni miran las
    monedas que se quedan en la curva. Solo comparando contra monedas del mismo
    molde que no corrieron se sabe si una wallet elige o si dispara a todo.

LIMITE HONESTO: la API no deja pasar de ~1.000 resultados por consulta, asi que
la ventana de ganadoras es aproximadamente el ultimo dia, no dos semanas.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from dotenv import load_dotenv

API = "https://frontend-api-v3.pump.fun"
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
WSOL = "So11111111111111111111111111111111111111112"
DESTINO = Path(__file__).parent / "top_compradores_listos.json"

# La mediana de una moneda migrada anda por $64K de ATH, asi que $100K no
# distingue nada. A partir de $1M si: es aproximadamente el 20% de arriba.
MIN_ATH = 1_000_000     # dolares de pico para considerarla ganadora
MAX_ATH = 500_000_000   # por encima de esto el dato de la API es basura
MAX_ATH_PERDEDORA = 100_000  # migro igual y se quedo ahi: mismo molde, sin premio
MIN_WSOL_POOL = 50.0    # SOL de verdad en el pool: sin esto el ATH es humo
N_GANADORAS = 45        # monedas ganadoras a diseccionar
N_CONTROL = 45          # perdedoras para medir a los bots indiscriminados
N_PRIMEROS = 8          # cuantos compradores por moneda se consideran "primeros"
MAX_TX_MIRAR = 70       # techo de transacciones leidas por moneda
MAX_PAGINAS_FIRMAS = 150  # techo de paginas de 1.000 firmas al retroceder
MIN_GANADORAS = 2       # con una sola coincidencia no se distingue de la suerte
MAX_TASA_CONTROL = 0.25  # si dispara a mas de 1 de cada 4 perdedoras, es un bot bobo

for _ruta in (Path(__file__).parent / ".env", Path("/root/ai-bot/.env")):
    if _ruta.is_file():
        load_dotenv(_ruta)
RPC = os.environ.get("SOLANA_NODE_RPC_ENDPOINT")
SESION = requests.Session()


def api(ruta: str, intentos: int = 4):
    """La API publica de pump.fun corta si se le aprieta: pausas y reintentos."""
    for i in range(intentos):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(API + ruta, headers=H), timeout=30))
        except Exception:  # noqa: BLE001
            time.sleep(0.5 + i * 1.5)
    return None


def rpc(metodo: str, params: list, intentos: int = 5):
    """El 429 se trata aparte: no es un fallo, es que hay que esperar mas."""
    for i in range(intentos):
        try:
            r = SESION.post(RPC, timeout=60, json={
                "jsonrpc": "2.0", "id": 1, "method": metodo, "params": params})
            if r.status_code == 429:
                time.sleep(0.8 + i * 1.5)
                continue
            d = r.json()
            if "error" in d:
                return None
            return d.get("result")
        except Exception:  # noqa: BLE001
            time.sleep(0.5 + i * 1.5)
    return None


def firmas_desde_el_principio(direccion: str):
    """Todas las firmas de una cuenta, de la mas antigua a la mas nueva.

    Solana solo sabe ir hacia atras (`before`), asi que hay que recorrerlo
    entero y darle la vuelta. Si la cuenta tiene mas movimiento del que cabe en
    el techo devuelve None: mejor descartar la moneda que analizar un trozo del
    medio creyendo que es el principio.
    """
    todas, before = [], None
    for _ in range(MAX_PAGINAS_FIRMAS):
        p = {"limit": 1000}
        if before:
            p["before"] = before
        r = rpc("getSignaturesForAddress", [direccion, p])
        if r is None:
            return None
        todas.extend(r)
        if len(r) < 1000:
            todas.reverse()
            return todas
        before = r[-1]["signature"]
    return None


def liquidez_wsol(pool: str) -> float:
    """SOL real dentro del pool. Es el unico numero de esta API que no se puede
    inflar: o esta el dinero o no esta."""
    r = rpc("getTokenAccountsByOwner", [pool, {"mint": WSOL}, {"encoding": "jsonParsed"}])
    total = 0.0
    for a in (r or {}).get("value", []):
        try:
            total += float(a["account"]["data"]["parsed"]["info"]
                           ["tokenAmount"]["uiAmount"] or 0)
        except Exception:  # noqa: BLE001
            pass
    return total


def primeros_compradores(moneda: dict):
    """Las primeras wallets que ponen dinero de verdad en esta moneda.

    Devuelve [(wallet, posicion, segundos_desde_el_lanzamiento, sol_gastado)].
    """
    mint = moneda["mint"]
    creador = moneda.get("creator")
    sigs = firmas_desde_el_principio(mint)
    if not sigs:
        return None
    # Las fallidas se tiran aqui, antes de gastar una llamada en leerlas: la
    # propia lista de firmas ya dice cual reventó.
    sigs = [s for s in sigs if not s.get("err")]
    if not sigs:
        return None
    t0 = sigs[0].get("blockTime") or 0

    salida, vistas = [], set()
    for s in sigs[:MAX_TX_MIRAR]:
        if len(salida) >= N_PRIMEROS:
            break
        tx = rpc("getTransaction", [s["signature"], {
            "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        time.sleep(0.05)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        claves = [k["pubkey"] if isinstance(k, dict) else k
                  for k in tx["transaction"]["message"]["accountKeys"]]
        pagador = claves[0]
        if pagador == creador or pagador in vistas:
            continue

        # sube el saldo del token DEL QUE PAGA -> ha comprado el.
        pre = {b["accountIndex"]: b for b in meta.get("preTokenBalances", [])}
        subida = 0.0
        for i, b in ((b["accountIndex"], b) for b in meta.get("postTokenBalances", [])):
            if b.get("mint") != mint or b.get("owner") != pagador:
                continue
            ahora = float(b["uiTokenAmount"].get("uiAmount") or 0)
            antes = float(pre.get(i, {}).get("uiTokenAmount", {}).get("uiAmount") or 0)
            subida += ahora - antes
        if subida <= 0:
            continue
        # y ademas suelta SOL. Si no lo suelta es un airdrop, no una compra.
        gasto = (meta["preBalances"][0] - meta["postBalances"][0]) / 1e9
        if gasto <= 0:
            continue

        vistas.add(pagador)
        salida.append((pagador, len(salida) + 1,
                       (s.get("blockTime") or t0) - t0, round(gasto, 4)))
    return salida


def leer_migradas() -> list[dict]:
    """Todas las monedas migradas que la API deja ver, de la mas nueva hacia atras.

    La API corta sobre el resultado 1.000, asi que esto son unas 24 h. No hay
    forma de llegar a 7-14 dias por aqui; se prefiere decirlo a fingirlo.
    """
    brutas, pagina = [], 0
    while pagina < 25:
        d = api(f"/coins?sort=created_timestamp&order=DESC&limit=50"
                f"&offset={pagina * 50}&complete=true")
        if not d:
            break
        brutas.extend(d)
        pagina += 1
        time.sleep(0.35)
    return brutas


def repartir_en_el_tiempo(monedas: list[dict], cuantas: int) -> list[dict]:
    """Coge `cuantas` repartidas por toda la ventana y no todas del mismo rato.

    Cuarenta monedas del mismo minuto son el mismo experimento cuarenta veces:
    los mismos bots estaban despiertos y las mismas wallets mirando.
    """
    if len(monedas) <= cuantas:
        return monedas
    orden = sorted(monedas, key=lambda c: -(c.get("created_timestamp") or 0))
    paso = len(orden) / cuantas
    return [orden[int(i * paso)] for i in range(cuantas)]


def elegir_ganadoras(migradas: list[dict]) -> list[dict]:
    """Las que corrieron de verdad: pico alto Y dinero todavia dentro del pool."""
    cand = [c for c in migradas if MIN_ATH < (c.get("ath_market_cap") or 0) < MAX_ATH]
    cand.sort(key=lambda c: -(c.get("ath_market_cap") or 0))
    buenas = []
    for c in cand:
        if len(buenas) >= N_GANADORAS:
            break
        pool = c.get("pump_swap_pool") or c.get("pool_address")
        if not pool:
            continue
        c["wsol_pool"] = round(liquidez_wsol(pool), 2)
        if c["wsol_pool"] < MIN_WSOL_POOL:
            continue
        buenas.append(c)
    return buenas


def elegir_perdedoras(migradas: list[dict], fuera: set[str]) -> list[dict]:
    """Mismo molde, sin premio: migraron igual pero se quedaron planas."""
    cand = [c for c in migradas
            if (c.get("ath_market_cap") or 0) < MAX_ATH_PERDEDORA
            and c["mint"] not in fuera]
    return repartir_en_el_tiempo(cand, N_CONTROL)


def diseccionar(monedas: list[dict], etiqueta: str) -> dict[str, list]:
    """Primeros compradores de cada moneda, en paralelo moderado."""
    res, fallos = {}, 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for c, r in zip(monedas, ex.map(primeros_compradores, monedas)):
            if r is None:
                fallos += 1
                continue
            res[c["mint"]] = r
    print(f"  {etiqueta}: {len(res)} monedas leidas, {fallos} descartadas "
          f"(historia demasiado larga o RPC caido)")
    return res


def main() -> None:
    if not RPC:
        print("Falta SOLANA_NODE_RPC_ENDPOINT en el .env")
        return

    print("Leyendo monedas migradas de pump.fun...")
    migradas = leer_migradas()
    if not migradas:
        print("La API no devuelve nada. Sin datos no hay lista.")
        return
    ahora = time.time() * 1000
    edades = [(ahora - (c.get("created_timestamp") or 0)) / 3_600_000 for c in migradas]
    ventana_h = round(max(edades), 1) if edades else 0
    # base: de cada cien monedas que migran, cuantas son ganadoras. Hace falta
    # para leer bien la tabla: acertar el 20% no es merito si el 20% gana solo.
    n_gana_total = sum(1 for c in migradas
                       if MIN_ATH < (c.get("ath_market_cap") or 0) < MAX_ATH)
    base = n_gana_total / len(migradas)
    print(f"  {len(migradas)} migradas, ventana de {ventana_h:.1f} h")
    print(f"  {n_gana_total} superan ${MIN_ATH:,} de ATH -> base del {base:.1%}\n")

    ganadoras = elegir_ganadoras(migradas)
    if not ganadoras:
        print("Ninguna moneda pasa el filtro de ganadora. Sin datos no hay lista.")
        return
    perdedoras = elegir_perdedoras(migradas, {c["mint"] for c in ganadoras})
    print(f"  {len(ganadoras)} ganadoras con pool vivo, {len(perdedoras)} perdedoras\n")

    print("Leyendo la cadena (esto tarda)...")
    compras_g = diseccionar(ganadoras, "ganadoras")
    compras_c = diseccionar(perdedoras, "perdedoras")
    if not compras_g:
        print("No se ha podido leer ninguna ganadora. No se escribe nada.")
        return

    fichas: dict[str, dict] = {}
    for mint, lista in compras_g.items():
        for w, pos, seg, sol in lista:
            f = fichas.setdefault(w, {"wallet": w, "monedas": [], "posiciones": [],
                                      "segundos": [], "sol": [], "control": 0})
            f["monedas"].append(mint)
            f["posiciones"].append(pos)
            f["segundos"].append(seg)
            f["sol"].append(sol)
    for lista in compras_c.values():
        for w, *_ in lista:
            if w in fichas:
                fichas[w]["control"] += 1

    n_control = max(1, len(compras_c))
    for f in fichas.values():
        f["ganadoras"] = len(f["monedas"])
        f["tasa_ganadoras"] = round(f["ganadoras"] / len(compras_g), 3)
        f["tasa_control"] = round(f["control"] / n_control, 3)
        f["posicion_media"] = round(statistics.mean(f["posiciones"]), 2)
        f["segundos_medios"] = round(statistics.mean(f["segundos"]), 1)
        f["sol_mediano"] = round(statistics.median(f["sol"]), 4)

    todas = sorted(fichas.values(), key=lambda f: (-f["ganadoras"], f["posicion_media"]))
    sel = [f for f in todas
           if f["ganadoras"] >= MIN_GANADORAS and f["tasa_control"] <= MAX_TASA_CONTROL]

    print(f"\n{len(todas)} wallets distintas entre los {N_PRIMEROS} primeros "
          f"compradores de {len(compras_g)} ganadoras")
    print(f"  {sum(1 for f in todas if f['ganadoras'] >= MIN_GANADORAS)} salen en "
          f"{MIN_GANADORAS}+ ganadoras")
    print(f"  {sum(1 for f in todas if f['tasa_control'] > MAX_TASA_CONTROL)} son bots "
          f"indiscriminados (tambien compran las muertas)")
    print(f"  = {len(sel)} candidatas\n")

    print("%-46s %5s %6s %7s %7s %6s %8s" % (
        "wallet", "gana", "%gana", "%muerta", "pos med", "seg", "SOL med"))
    print("-" * 96)
    for f in todas[:30]:
        marca = "  <-- dispara a todo" if f["tasa_control"] > MAX_TASA_CONTROL else ""
        print("%-46s %5d %5.0f%% %6.0f%% %7.2f %6.1f %8.3f%s" % (
            f["wallet"], f["ganadoras"], f["tasa_ganadoras"] * 100,
            f["tasa_control"] * 100, f["posicion_media"], f["segundos_medios"],
            f["sol_mediano"], marca))
    print("-" * 96)

    DESTINO.write_text(json.dumps({
        "generado": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "criterio": (
            f"wallets entre los {N_PRIMEROS} primeros compradores REALES (firman, "
            f"pagan y les sube el saldo del token) de >= {MIN_GANADORAS} monedas "
            f"migradas con ATH entre ${MIN_ATH:,} y ${MAX_ATH:,} y >= "
            f"{MIN_WSOL_POOL:g} WSOL de liquidez real en el pool; descartadas las "
            f"que aparecen tambien en mas del {MAX_TASA_CONTROL:.0%} de una muestra "
            f"de monedas muertas"),
        "limitaciones": (
            "1) SESGO DE SUPERVIVENCIA: la lista se construye mirando ganadoras, "
            "asi que no mide el acierto de estas wallets, solo su presencia. El "
            "campo tasa_control es el unico contrapeso y esta medido sobre una "
            "muestra pequena de monedas muertas. "
            "2) La API de pump.fun no deja pasar de ~1.000 resultados, asi que la "
            "ventana real son las ultimas ~24 h, no 7-14 dias. "
            "3) ath_market_cap de la API es precio x 1e9 y se dispara con pools "
            "vacios; por eso se acota y se exige liquidez, pero sigue sin ser una "
            "medida fiable de cuanto subio la moneda. "
            "4) Casi todos los lanzamientos actuales hacen create+compra+migracion "
            "en el mismo bloque, asi que 'primer comprador' significa primer "
            "comprador del pool de PumpSwap, no de la curva. "
            "5) Las monedas analizadas tienen horas de vida: una wallet puede "
            "estar aun dentro y perder despues."),
        "ganadoras_analizadas": len(compras_g),
        "control_analizadas": len(compras_c),
        "wallets": [f["wallet"] for f in sel],
        "detalle": [{
            "wallet": f["wallet"],
            "ganadoras": f["ganadoras"],
            "monedas": f["monedas"],
            "posicion_media": f["posicion_media"],
            "segundos_medios": f["segundos_medios"],
            "sol_mediano": f["sol_mediano"],
            "muertas_del_control": f["control"],
            "tasa_control": f["tasa_control"],
        } for f in sel],
        "descartadas_por_disparar_a_todo": [{
            "wallet": f["wallet"], "ganadoras": f["ganadoras"],
            "tasa_control": f["tasa_control"],
        } for f in todas if f["tasa_control"] > MAX_TASA_CONTROL],
    }, indent=1), encoding="utf-8")
    print(f"guardado en {DESTINO}")


if __name__ == "__main__":
    main()
