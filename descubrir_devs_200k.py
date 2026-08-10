"""Busca los devs de pump.fun que han hecho al menos un pelotazo de $200K
SIN ser granjas de spam.

Por que esta lista y no la de `descubrir_devs_cadencia.py`:

  - La lista de cadencia ordena por tasa de migracion. Migrar esta bien, pero
    una moneda puede migrar y morir en $70K: el bot compra, la curva se acaba
    y no hay a quien vendersela. Lo que de verdad paga es el TECHO, y el techo
    se mide con `ath_market_cap`.
  - Aqui el corte es duro: el dev tiene que haber tocado $200K alguna vez.
  - Y ademas se le exige no ser spammer. Un tio que suelta 15 monedas al dia
    acaba pegandola por puro numero, no por criterio; seguirle es pagar
    comisiones en 14 basuras para acertar una. Por eso se pide entre 0.5 y 2.5
    lanzamientos diarios: alguien que trabaja cada moneda pero sigue vivo.

Limitaciones de la API (medidas, no supuestas):

  - /coins devuelve COMO MUCHO 70 elementos por peticion, aunque pidas
    limit=200. Con `?creator=` eso significa que de un dev con 300 monedas
    solo vemos las 70 mas recientes. Todas las metricas historicas
    (lanzamientos, tasa, ATH) son por tanto "sobre sus ultimas <=70 monedas".
    Para el filtro anti-spam da igual (mira solo 7 dias); para la tasa de
    migracion es un sesgo a favor del dev si antes era peor.
  - `ath_market_cap` viene sucio: hay monedas viejas con valores de 1e20 o
    1e23 dolares, que es un bug del backend. Se descarta todo lo que pase de
    TECHO_CREIBLE en vez de fiarse.
  - Y aun limpiando, el ATH es PRECIO, no dinero. Comprobado con la liquidez
    real de GMGN sobre dos monedas del mismo dev (6Wg4aeZ2):
      * eNUDev8...pump decia $13.870.000 de ATH -> hoy su pool tiene $392 y
        nacio con 85 SOL. Para marcar eso no hizo falta que entraran millones,
        basto con empujar un pool seco.
      * EPHaZBi...pump dice $8.250.000 AHORA -> pool de $209.000 de verdad.
        Ese si se puede tocar, aunque sacar mas de unas decenas de miles se
        come el precio.
    O sea: el ATH exagera lo cobrable como en un orden de magnitud. La lista
    dice "este dev ha hecho monedas que MARCARON $200K", no "se pudo cobrar".
    Para saber lo segundo hay que mirar el pool moneda a moneda.
  - `reply_count` viene siempre a 0 en esta version de la API, o sea que no
    sirve para distinguir un pelotazo con gente de un pico fantasma.
  - `sort=ath_market_cap&order=DESC` SI funciona, pero las primeras paginas
    son justo esa basura de 1e2x, asi que sirve poco. El que manda es
    `sort=market_cap&order=DESC`, que pagina hondo y limpio (en el offset
    1000 va por $160K, o sea que cubre de sobra el corte de $200K).

De donde salen los candidatos (tres fuentes, porque una sola no basta):

  1. CREACIONES RECIENTES: sin esto la lista sale llena de devs de 2024 que
     la pegaron una vez y llevan un anyo sin lanzar. El filtro de 7 dias
     mataria a casi todos y nos quedariamos sin nadie.
  2. MARKET CAP DESC: los que estan grandes AHORA.
  3. ATH DESC: los que estuvieron grandes ALGUNA VEZ.

  Las fuentes 2 y 3 aportan calidad, la 1 aporta que el bot tenga a quien
  disparar esta semana.
"""

import json
import os
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://frontend-api-v3.pump.fun"
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

PAGINA = 50          # la API corta en 70; con 50 se pagina limpio
PAG_RECIENTES = 24   # 24 x 50 = 1200 creaciones recientes -> devs vivos
PAG_MC = 18          # 18 x 50 = 900 monedas grandes por market cap actual
PAG_ATH = 18         # otras 900 por maximo historico

UMBRAL_ATH = 200_000     # el pelotazo que pidio Manu
MIN_LANZAMIENTOS = 5     # con 2 monedas cualquier porcentaje es ruido
DIA_MIN, DIA_MAX = 0.5, 2.5   # ventana anti-spam, lanzamientos/dia a 7 dias

# TECHO_CREIBLE: sacado de mirar el listado, no a ojo. La moneda mas grande
# viva anda por $175M y el ATH mas alto que se le ve a una moneda que sigue
# siendo grande es $450M. Por encima de eso el campo escupe basura -- se
# vieron $428.000M y hasta 1e23 -- y tambien monedas muertas de $2.000 que
# dicen haber marcado $870M. Se corta en $500M: justo por encima del record
# real observado, asi que un pelotazo de verdad cabe y el bug no.
TECHO_CREIBLE = 5e8

# RAFAGA_DIA: el filtro de 0.5-2.5 al dia es una MEDIA de 7 dias, y una media
# se deja enganyar: el que suelta 10 monedas un martes y descansa el resto de
# la semana sale a 1.4/dia y parece un artesano. Se guarda el peor dia para
# poder verlo, y se marca al que pasa de aqui.
RAFAGA_DIA = 5

SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "top_devs_200k.json")

MS_DIA = 86_400_000


def get(url, intentos=5):
    """GET con reintentos y espera creciente.

    La API no tiro ni un 429 en pruebas de 15 peticiones seguidas, pero como
    esto son >1500 peticiones se deja el backoff puesto: si empieza a cortar,
    el script frena solo en vez de morirse a la mitad.
    """
    for i in range(intentos):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=H), timeout=30))
        except Exception:
            if i == intentos - 1:
                return None
            time.sleep(1.5 + i * 2)


def ath_limpio(moneda):
    """ATH en dolares de una moneda, o None si el dato no es creible.

    Si falta el ATH se usa el market cap actual como suelo: una moneda ha
    valido por lo menos lo que vale ahora mismo. Nunca al reves.
    """
    ath = float(moneda.get("ath_market_cap") or 0)
    ahora = float(moneda.get("usd_market_cap") or 0)
    mejor = max(ath, ahora)
    if mejor >= TECHO_CREIBLE:
        return None          # dato roto: ni cuenta como acierto ni como cero
    return mejor


def recolectar(sort, paginas, etiqueta):
    """Baja N paginas de un listado y devuelve {creador: monedas_vistas}."""
    devs = {}
    for p in range(paginas):
        d = get(f"{API}/coins?sort={sort}&order=DESC"
                f"&limit={PAGINA}&offset={p * PAGINA}")
        if not d or not isinstance(d, list):
            break
        for c in d:
            cre = c.get("creator")
            if cre:
                devs[cre] = devs.get(cre, 0) + 1
        print(f"  [{etiqueta}] pagina {p + 1}/{paginas} -> "
              f"{len(devs)} devs distintos", end="\r")
        time.sleep(0.4)
    print(f"  [{etiqueta}] {len(devs)} devs distintos" + " " * 20)
    return devs


def ficha(cre):
    """Mide el historial de un dev. Una peticion, <=70 monedas."""
    d = get(f"{API}/coins?creator={cre}&limit=200&offset=0")
    if not d or not isinstance(d, list) or not d:
        return None

    lanz = len(d)
    migs = sum(1 for x in d if x.get("complete") is True)

    ts = sorted((x.get("created_timestamp") or 0) for x in d)
    ahora = time.time() * 1000
    ult7 = [t for t in ts if t > ahora - 7 * MS_DIA]

    # PEOR DIA: se parten los ultimos 7 dias en ventanas de 24h y se mira la
    # mas cargada. Es lo que desenmascara al que va a rafagas.
    por_dia = [0] * 7
    for t in ult7:
        i = int((ahora - t) // MS_DIA)
        if 0 <= i < 7:
            por_dia[i] += 1
    max_dia = max(por_dia) if ult7 else 0

    # HUECO MEDIANO: se usa mediana y no media porque estos devs van a
    # rafagas -- cinco monedas en una hora y luego tres dias parados -- y la
    # media cae en un hueco donde no hay ni un lanzamiento.
    huecos = [(ts[i + 1] - ts[i]) / 3_600_000 for i in range(len(ts) - 1)]
    hueco_mediano = statistics.median(huecos) if huecos else None

    todos = [ath_limpio(x) for x in d]
    aths = [a for a in todos if a is not None and a > 0]
    sobre = sum(1 for a in aths if a >= UMBRAL_ATH)

    return {
        "dev": cre,
        "lanzamientos": lanz,
        "migradas": migs,
        "tasa": migs / lanz,
        "dia_7d": len(ult7) / 7.0,
        "max_dia_7d": max_dia,
        "rafagas": max_dia >= RAFAGA_DIA,
        "hueco_mediano_h": round(hueco_mediano, 2) if hueco_mediano else None,
        "ath_medio": round(statistics.mean(aths)) if aths else 0,
        "ath_mediano": round(statistics.median(aths)) if aths else 0,
        "ath_max": round(max(aths)) if aths else 0,
        "monedas_sobre_200k": sobre,
        "ath_descartados": sum(1 for a in todos if a is None),
        "ultimo_lanzamiento_h": round((ahora - ts[-1]) / 3_600_000, 1),
        "muestra_topada": lanz >= 70,   # la API corto: hay mas historial
    }


def main():
    print("1) Juntando candidatos de tres listados distintos")
    candidatos = {}
    for sort, pags, etq in (("created_timestamp", PAG_RECIENTES, "recientes"),
                            ("market_cap", PAG_MC, "market cap"),
                            ("ath_market_cap", PAG_ATH, "ATH")):
        for dev, n in recolectar(sort, pags, etq).items():
            candidatos[dev] = candidatos.get(dev, 0) + n
    print(f"   total: {len(candidatos)} devs a medir\n")

    print("2) Midiendo historial de cada dev (una peticion por dev)")
    fichas = []
    # 5 hilos como maximo: es el limite que se fijo para no tirar la API.
    with ThreadPoolExecutor(max_workers=5) as ex:
        for i, f in enumerate(ex.map(ficha, list(candidatos))):
            if f:
                fichas.append(f)
            if (i + 1) % 50 == 0:
                print(f"   {i + 1}/{len(candidatos)}", end="\r")
    print(f"   {len(fichas)} devs medidos" + " " * 20 + "\n")

    # Los filtros se aplican en cascada y se cuenta cuantos caen en cada uno,
    # para poder decir con honestidad donde se muere la lista.
    con_200k = [f for f in fichas if f["monedas_sobre_200k"] >= 1]
    con_hist = [f for f in con_200k if f["lanzamientos"] >= MIN_LANZAMIENTOS]
    buenos = [f for f in con_hist if DIA_MIN <= f["dia_7d"] <= DIA_MAX]

    print("3) Embudo de filtros")
    print(f"   medidos ................... {len(fichas)}")
    print(f"   con una moneda >$200K ..... {len(con_200k)}")
    print(f"   con {MIN_LANZAMIENTOS}+ lanzamientos ....... {len(con_hist)}")
    print(f"   cadencia {DIA_MIN}-{DIA_MAX}/dia (no spam) {len(buenos)}\n")

    # Orden: primero cuantos pelotazos ha metido, luego lo alto que suele
    # llegar. El numero de aciertos manda sobre un ATH maximo suelto, que
    # puede ser suerte de una sola moneda.
    buenos.sort(key=lambda f: (-f["monedas_sobre_200k"], -f["ath_mediano"],
                               -f["tasa"]))

    # Manu pidio fuera a los que sueltan 5 o 10 al dia. El filtro de media
    # semanal no los pilla (10 monedas un martes = 1.4/dia), asi que la lista
    # que se come el bot es la limpia y los de rafagas van aparte, medidos
    # pero apartados: cambiar de una lista a otra es una linea.
    limpios = [f for f in buenos if not f["rafagas"]]
    con_rafagas = [f for f in buenos if f["rafagas"]]

    print("%-46s %5s %5s %6s %8s %7s %11s %11s %6s" % (
        "dev", "lanz", "migr", "tasa", "lanz/dia", "peor d", "ATH mediano",
        "ATH max", ">200K"))
    print("-" * 122)
    for f in buenos:
        print("%-46s %5d %5d %5.1f%% %8.2f %7s %11s %11s %6d" % (
            f["dev"], f["lanzamientos"], f["migradas"], f["tasa"] * 100,
            f["dia_7d"],
            ("%d !" % f["max_dia_7d"]) if f["rafagas"] else str(f["max_dia_7d"]),
            "$%s" % format(f["ath_mediano"], ","),
            "$%s" % format(f["ath_max"], ","),
            f["monedas_sobre_200k"]))
    print("-" * 122)
    print("%d devs pasan los tres filtros -> %.1f lanzamientos/dia" % (
        len(buenos), sum(f["dia_7d"] for f in buenos)))
    print("de esos, %d van a rafagas (peor dia >= %d monedas, marcados con !) "
          "y quedan FUERA de 'wallets'" % (len(con_rafagas), RAFAGA_DIA))
    print("LISTA FINAL: %d devs -> %.1f lanzamientos/dia esperados" % (
        len(limpios), sum(f["dia_7d"] for f in limpios)))

    with open(SALIDA, "w", encoding="utf-8") as fh:
        json.dump({
            "generado": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "criterio": (
                "devs con al menos una moneda por encima de $200K de ATH y "
                f"entre {DIA_MIN} y {DIA_MAX} lanzamientos al dia en los "
                f"ultimos 7 dias (minimo {MIN_LANZAMIENTOS} lanzamientos de "
                f"historial), quitando ademas a los que algun dia soltaron "
                f"{RAFAGA_DIA} monedas o mas"),
            "limitaciones": (
                "la API devuelve como maximo 70 monedas por creador, asi que "
                "el historial es el de sus ultimas <=70 monedas; los ATH por "
                f"encima de ${TECHO_CREIBLE:,.0f} se descartan por ser un bug "
                "conocido del backend de pump.fun; el ATH es precio y no "
                "liquidez, no esta comprobado que se pudiera vender ahi; "
                "dia_7d es una media, mirar max_dia_7d y rafagas para saber "
                "si el dev trabaja seguido o a golpes"),
            "candidatos_medidos": len(fichas),
            "esperados_dia": round(sum(f["dia_7d"] for f in limpios), 1),
            "wallets": [f["dev"] for f in limpios],
            "wallets_con_rafagas": [f["dev"] for f in con_rafagas],
            "detalle": buenos,
        }, fh, indent=1)
    print(f"\nguardado en {SALIDA}")


if __name__ == "__main__":
    main()
