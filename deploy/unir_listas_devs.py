"""Junta todas las listas de devs que hay sueltas en una sola, con su origen.

EL PROBLEMA QUE RESUELVE. En la raiz del repo hay seis ficheros de devs,
hechos en fechas distintas y midiendo cosas distintas: uno ordena por mediana
de pico, otro por lanzamientos en 7 dias, otro por monedas que pasaron de
200K. Ninguno lo lee el bot que esta corriendo. Y como cada uno mide una cosa,
no se puede decir "estos son los buenos" mirando uno solo.

LA SENAL QUE SI SE PUEDE SACAR SIN RED: en cuantas listas aparece cada dev.
Cada lista se hizo con un criterio distinto y en un momento distinto, asi que
un dev que sale en tres pasa tres examenes diferentes. No demuestra que no
vaya a hacer rug --eso hay que medirlo-- pero es la mejor ordenacion posible
con lo que ya hay guardado.

LO QUE ESTO NO ES. No es una lista de "devs que nunca hacen rug". Ninguno de
los ficheros de origen mide rugs, ni pagos de DexScreener, ni boosts, ni
redes. Para eso esta `medir_devs.py`, que hay que dejar corriendo en el
droplet porque necesita hablar con las APIs.

    uv run deploy/unir_listas_devs.py

Escribe devs_unificados.json y no toca ninguno de los originales.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SALIDA = Path("devs_unificados.json")

#: Fichero -> (etiqueta, que midio). La fecha va en la etiqueta porque el
#: paso del tiempo es el mayor riesgo de estas listas: un dev bueno en julio
#: puede llevar dos meses sin lanzar.
FUENTES: dict[str, tuple[str, str]] = {
    "top_devs_quality.json": (
        "quality (jul-2026)", "mediana de pico 15-25K, >=3 lanzamientos/7d"),
    "top_devs_premium.json": (
        "premium (jul-2026)", "lanzamientos fuertes organicos, mediana pico >=8K"),
    "top_devs_init4k.json": (
        "init4k (jul-2026)", "MC inicial <4K (lanzamiento limpio) + pico >12K"),
    "top_devs_nextbig.json": (
        "nextbig (jul/ago-2026)", "mediana >=25K, o repite monedas de +200K"),
    "top_devs_no_mayhem.json": (
        "no_mayhem (ago-2026)", "sin mayhem, >=5 lanzamientos/2h, >=20% sobre 8K"),
}
#: Listas de EXCLUSION. Mandan sobre todo lo demas.
VETOS: dict[str, str] = {
    "blacklist_devs_medidos.json": "blacklist medida (ago-2026): <5% de sus monedas pasan de 8K",
}
#: Devs que se vio pagar DexScreener. Es lo mas cerca que hay hoy de "suele
#: pagar dex", pero es una foto suelta, no una tasa.
PAGADORES = Path("devs_dex_payers.txt")
#: A partir de cuantas listas se imprime el dev uno por uno. Con una sola
#: lista son cientos y no aportan: lo que distingue es coincidir.
DETALLE_DESDE = 2


def _wallets(datos: object) -> list[tuple[str, dict]]:
    """Saca (wallet, detalle) de cualquiera de los formatos que hay."""
    fuera: list[tuple[str, dict]] = []
    if isinstance(datos, dict):
        for clave in ("wallets", "detalle"):
            for item in datos.get(clave) or []:
                if isinstance(item, str):
                    fuera.append((item, {}))
                elif isinstance(item, dict) and item.get("wallet"):
                    fuera.append((str(item["wallet"]), item))
    return fuera


def _leer(ruta: Path) -> object:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> None:  # noqa: C901, PLR0912
    """Une las listas, cuenta coincidencias y aparta a los vetados."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    devs: dict[str, dict] = {}
    for fichero, (etiqueta, criterio) in FUENTES.items():
        datos = _leer(Path(fichero))
        if datos is None:
            print(f"  (no encontrado: {fichero})")
            continue
        encontrados = _wallets(datos)
        print(f"  {etiqueta:<24} {len(encontrados):>4} devs  ·  {criterio}")
        for wallet, detalle in encontrados:
            d = devs.setdefault(wallet, {"wallet": wallet, "listas": [], "datos": {}})
            if etiqueta not in d["listas"]:
                d["listas"].append(etiqueta)
            for campo in ("median_peak", "launches_7d", "per_day",
                          "coins_over_200k", "best_peak_usd", "pct_8k"):
                if detalle.get(campo) is not None:
                    d["datos"].setdefault(campo, detalle[campo])

    vetados: dict[str, str] = {}
    for fichero, motivo in VETOS.items():
        datos = _leer(Path(fichero))
        for wallet, _ in _wallets(datos):
            vetados[wallet] = motivo
    print(f"  {'vetados':<24} {len(vetados):>4} devs")

    pagadores: set[str] = set()
    if PAGADORES.exists():
        pagadores = {
            linea.strip() for linea in PAGADORES.read_text(encoding="utf-8").splitlines()
            if linea.strip() and not linea.startswith("#")
        }
    print(f"  {'pagaron dexscreener':<24} {len(pagadores):>4} devs")

    for wallet, d in devs.items():
        d["n_listas"] = len(d["listas"])
        d["pago_dex"] = wallet in pagadores
        d["vetado"] = vetados.get(wallet)

    limpios = [d for d in devs.values() if not d["vetado"]]
    limpios.sort(key=lambda d: (d["n_listas"], d["pago_dex"]), reverse=True)

    print(f"\n{'=' * 70}")
    print(f"{len(devs)} devs distintos · {len(limpios)} sin veto")
    print(f"{'=' * 70}")
    for n in range(max((d["n_listas"] for d in limpios), default=0), 0, -1):
        grupo = [d for d in limpios if d["n_listas"] == n]
        if not grupo:
            continue
        print(f"\nEN {n} LISTA(S): {len(grupo)} devs")
        if n >= DETALLE_DESDE:
            for d in grupo:
                marca = "  [pago dex]" if d["pago_dex"] else ""
                print(f"  {d['wallet']}  {', '.join(d['listas'])}{marca}")

    salida = {
        "_README": (
            "Union de las listas de devs que habia sueltas. `n_listas` es en "
            "cuantas aparece: cada lista se hizo con un criterio distinto, asi "
            "que salir en varias es pasar varios examenes. NO mide rugs, ni "
            "pagos de dex, ni si sigue activo -- para eso, medir_devs.py."
        ),
        "_fuentes": {k: v[0] for k, v in FUENTES.items()},
        "_count": len(limpios),
        "wallets": limpios,
        "vetados": [{"wallet": w, "motivo": m} for w, m in vetados.items()],
    }
    SALIDA.write_text(json.dumps(salida, indent=2), encoding="utf-8")
    print(f"\nEscrito en {SALIDA}")
    print("\nOJO CON LA FECHA: casi todo esto es de julio y agosto. Un dev bueno")
    print("entonces puede llevar dos meses sin lanzar. Antes de usarlo para")
    print("comprar hay que pasarle medir_devs.py, que mira como estan HOY.")


if __name__ == "__main__":
    main()
