"""Que habrian dado los tres corredores de Manu con las salidas de HOY.

Simula vela a vela (30 s, GMGN) sobre los tres lanzamientos que paso el 10-ago,
aplicando las reglas exactas de cada estrategia: entrada cuando el market cap
cae por debajo del techo, stop de entrada, take-profit y trailing desde el pico.

No es un backtest de la ENTRADA -- esas tres ya sabemos que las habria pillado,
por eso las eligio. Es un backtest de la SALIDA, que es lo que se ha estado
tocando a ciegas todo el dia.

Aviso sobre lo que este numero NO incluye: comision de pump.fun (~1% por lado),
comision de prioridad y, sobre todo, el impacto de vender. Con velas de 30 s se
supone que se vende al precio de la vela, y en la realidad se vende peor. El
resultado de aqui es el TECHO de lo que se habria sacado, no lo que habria
entrado en la cartera.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request

TOTAL_SUPPLY = 1_000_000_000
POSICION = 0.15  # SOL

TOKENS = {
    "Dadjak": "7Gg7eLzykpyafK2aGHK8buFbFouHzMQ6KchxtKx5pump",
    "FROGGY": "7SNuFkbD7aVs5LMM6DWsSFTq4zsXNxYuD8Hdsp1Kpump",
    "Kermit": "Y8ETVJQHSebU6JNroJW3gefhCLAP71KJfsbVHhapump",
}

ESTRATEGIAS = {
    "PREMIUM": dict(techo_mc=5500, tp=0.70, tp_vende=0.60, stop=0.50, trail=0.33),
    "CORREDORES": dict(techo_mc=5500, tp=0.45, tp_vende=0.75, stop=0.50, trail=0.33),
    # Lo que habia esta manana, para ver si el cambio sirvio de algo.
    "(la de ayer)": dict(techo_mc=5500, tp=1.00, tp_vende=0.50, stop=0.33, trail=0.33),
}


def nacimiento(addr: str) -> int | None:
    """Segundos unix en que se creo la moneda, de la API de pump.fun."""
    try:
        req = urllib.request.Request(
            f"https://frontend-api-v3.pump.fun/coins/{addr}",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        d = json.load(urllib.request.urlopen(req, timeout=25))
        return int((d.get("created_timestamp") or 0) / 1000) or None
    except Exception:
        return None


def velas(addr: str) -> list[dict] | None:
    """Velas de 30 s DESDE EL NACIMIENTO.

    Sin --from/--to, GMGN devuelve la ventana reciente. Con una moneda de hace
    horas eso deja fuera justo el tramo que interesa -- el del lanzamiento -- y
    la simulacion cree que nunca hubo un precio de entrada.
    """
    t0 = nacimiento(addr)
    cmd = ["gmgn-cli", "market", "kline", "--chain", "sol",
           "--address", addr, "--resolution", "30s", "--raw"]
    if t0:
        cmd += ["--from", str(t0 - 60), "--to", str(t0 + 3 * 3600)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=90, shell=True).stdout
        return json.loads(out[out.index("{"):])["list"]
    except Exception:
        return None


def simular(vs: list[dict], cfg: dict) -> tuple[float, str]:
    """Devuelve (multiplicador sobre lo invertido, como acabo)."""
    # Se entra por la APERTURA de la vela, no por el cierre. El bot compra en
    # los primeros 7-15 s y una vela de 30 s puede abrir en 2.500 y cerrar en
    # 11.000: mirar el cierre dejaba fuera a FROGGY, que es justo la que mas
    # corrio. La apertura es lo que el bot ve cuando decide.
    entrada = None
    i_ent = 0
    for j, v in enumerate(vs):
        if float(v["open"]) * TOTAL_SUPPLY <= cfg["techo_mc"]:
            entrada, i_ent = float(v["open"]), j
            break
    if entrada is None:
        return 0.0, "nunca entro (siempre por encima del techo)"

    queda = 1.0          # fraccion de la posicion que sigue dentro
    cobrado = 0.0        # en multiplos de la inversion
    pico = entrada
    tp_hecho = False
    motivo = "se acabaron las velas"

    for v in vs[i_ent:]:
        alto, bajo, cierre = (float(v["high"]), float(v["low"]), float(v["close"]))
        pico = max(pico, alto)

        # 1) Stop de entrada, solo mientras no se haya cobrado el TP.
        if not tp_hecho and bajo <= entrada * (1 - cfg["stop"]):
            cobrado += queda * (1 - cfg["stop"])
            queda = 0.0
            motivo = f"stop de entrada -{cfg['stop'] * 100:.0f}%"
            break

        # 2) Take-profit.
        if not tp_hecho and alto >= entrada * (1 + cfg["tp"]):
            cobrado += cfg["tp_vende"] * (1 + cfg["tp"])
            queda -= cfg["tp_vende"]
            tp_hecho = True

        # 3) Trailing, armado tras subir un 20%.
        if queda > 0 and pico >= entrada * 1.2:
            listo = pico * (1 - cfg["trail"])
            if bajo <= listo:
                cobrado += queda * (listo / entrada)
                queda = 0.0
                motivo = f"trailing desde el pico (x{pico / entrada:.1f})"
                break

    if queda > 0:
        cobrado += queda * (float(vs[-1]["close"]) / entrada)
    return cobrado, motivo


print(f"Posicion simulada: {POSICION} SOL   -   pico real de cada moneda entre parentesis\n")

totales = {n: 0.0 for n in ESTRATEGIAS}
for nombre, addr in TOKENS.items():
    vs = velas(addr)
    if not vs:
        print(f"{nombre}: sin datos")
        continue
    ent = next((float(v["open"]) for v in vs
                if float(v["open"]) * TOTAL_SUPPLY <= 5500), None)
    if ent is None:
        # GMGN solo sirve una ventana reciente de velas: pasadas unas horas ya
        # no se puede recuperar el tramo del lanzamiento, que es el unico que
        # vale para esto. No se inventa un resultado.
        print("=== " + nombre + ": GMGN ya no da las velas de su nacimiento, "
              "no se puede simular ===")
        print()
        continue
    pico = max(float(v["high"]) for v in vs)
    x = pico / ent if ent else 0
    print(f"=== {nombre}  (entrada ${ent * TOTAL_SUPPLY:,.0f} -> pico "
          f"${pico * TOTAL_SUPPLY:,.0f}, x{x:.1f}) ===")
    for est, cfg in ESTRATEGIAS.items():
        mult, motivo = simular(vs, cfg)
        gan = (mult - 1) * POSICION
        totales[est] += gan
        print("  %-13s %6.2fx  ->  %+.4f SOL   (%s)" % (est, mult, gan, motivo))
    print()

print("=" * 62)
print("TOTAL de las tres, con %.2f SOL por operacion:" % POSICION)
for est, t in sorted(totales.items(), key=lambda x: -x[1]):
    print("  %-13s %+.4f SOL   (%.0f%% sobre %.2f invertidos)"
          % (est, t, t / (POSICION * len(TOKENS)) * 100, POSICION * len(TOKENS)))
