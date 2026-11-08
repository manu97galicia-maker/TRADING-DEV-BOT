"""Devs MAYHEM frescos de Dune y una lista con ~200 lanzamientos al dia.

Pedido el 8-nov: "consulta a Dune primero si tiene mas o si esta
actualizado. Quiero unos 200 lanzamientos diarios si puede ser" (para la
estrategia mayhem 400-2000%, que ahora sigue a 57 devs de un estudio viejo).

QUE HACE
1. Una consulta a Dune (deploy/dune/mayhem_devs.sql) con el precio de SOL
   de hoy. Dice si Dune esta al dia (su ultima creacion de pump.fun) y
   cuantas monedas mayhem se lanzan al dia en TOTAL.
2. Ordena los devs por calidad (que % de sus monedas llego a 100k, luego a
   30k, luego su mediana) y los va sumando hasta llegar al objetivo de
   lanzamientos al dia, contados con su ritmo de los ultimos 7 dias. Fuera
   los que venden en los primeros 30 min en mas de la mitad de sus monedas.
3. Escribe /root/devs_mayhem_dune.json (la lista) y deja el crudo en
   /root/mayhem_dune_crudo.json (con --sin-dune se rehace sin gastar).

No compra, no vende, no toca el bot ni su configuracion. La estrategia sigue
con sus 57 devs hasta que Manu decida cambiar a esta lista.

    cd /root/ai-bot && .venv/bin/python deploy/dune_mayhem.py
    cd /root/ai-bot && .venv/bin/python deploy/dune_mayhem.py --objetivo 150
    cd /root/ai-bot && .venv/bin/python deploy/dune_mayhem.py --sin-dune
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SQL = Path(__file__).resolve().parent / "dune" / "mayhem_devs.sql"
CRUDO = Path("/root/mayhem_dune_crudo.json")
SALIDA = Path("/root/devs_mayhem_dune.json")
#: Fuera quien vende en los primeros 30 min en mas de este % de sus monedas.
MAX_PCT_RUG = 50.0


def _dune_mod():  # noqa: ANN202
    """Reutiliza la conexion a Dune de dune_devs_mes.py (clave, envio, espera)."""
    ruta = Path(__file__).resolve().parent / "dune_devs_mes.py"
    spec = importlib.util.spec_from_file_location("dune_devs_mes", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _f(fila: dict, k: str) -> float:
    try:
        return float(fila.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


def al_dia(fila: dict) -> float:
    """Lanzamientos mayhem al dia de un dev, con su ritmo de los ultimos 7 dias."""
    return _f(fila, "lanz_7d") / 7.0


def ordena(filas: list[dict]) -> list[dict]:
    """Los mejores primero; fuera los que venden pronto en mas de la mitad."""
    limpios = [f for f in filas if _f(f, "pct_rug") <= MAX_PCT_RUG]
    return sorted(
        limpios,
        key=lambda f: (-_f(f, "pct_100k"), -_f(f, "pct_30k"), -_f(f, "mediana_usd")),
    )


def elige(filas: list[dict], objetivo: float) -> list[dict]:
    """Suma devs, de mejor a peor, hasta llegar a `objetivo` lanzamientos/dia.

    Solo cuentan los que han lanzado algo en los ultimos 7 dias: un dev que
    no lanza no da oportunidades. Si no se llega, salen todos los activos.
    """
    out: list[dict] = []
    total = 0.0
    for f in ordena(filas):
        if al_dia(f) <= 0:
            continue
        out.append(f)
        total += al_dia(f)
        if total >= objetivo:
            break
    return out


def resumen(devs: list[dict]) -> dict:
    """Calidad media de un grupo, pesada por lanzamientos."""
    n = sum(_f(f, "lanz_30d") for f in devs) or 1.0
    return {
        "devs": len(devs),
        "lanz_dia": round(sum(al_dia(f) for f in devs), 1),
        "pct_100k": round(
            sum(_f(f, "pct_100k") * _f(f, "lanz_30d") for f in devs) / n, 1
        ),
        "pct_30k": round(
            sum(_f(f, "pct_30k") * _f(f, "lanz_30d") for f in devs) / n, 1
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--objetivo", type=float, default=200.0, help="lanzamientos/dia")
    p.add_argument("--sin-dune", action="store_true", help="rehace sin gastar creditos")
    p.add_argument("--rendimiento", default="medium", choices=["medium", "large"])
    a = p.parse_args()

    if a.sin_dune:
        crudo = json.loads(CRUDO.read_text(encoding="utf-8"))
    else:
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv(RAIZ / ".env")
        d = _dune_mod()
        clave = d._clave()  # noqa: SLF001
        if not clave:
            print("No encuentro la clave de Dune en el .env (una variable con DUNE).")
            raise SystemExit(2)
        sol = d.precio_sol()
        print(f"== DUNE mayhem (SOL a {sol:.2f} $). Puede tardar varios minutos.")
        sql = SQL.read_text(encoding="utf-8").replace("{SOL_USD}", f"{sol:.4f}")
        try:
            filas = d.dune_sql(sql, clave, a.rendimiento)
        except Exception as e:  # noqa: BLE001
            print(f"FALLO: {e if isinstance(e, RuntimeError) else d._error(e)}")  # noqa: SLF001
            raise SystemExit(1) from None
        crudo = {"ts": time.time(), "sol": sol, "filas": filas}
        CRUDO.write_text(json.dumps(crudo), encoding="utf-8")

    filas = crudo["filas"]
    if not filas:
        print("Dune no devolvio ningun dev mayhem con 2+ lanzamientos.")
        raise SystemExit(1)
    f0 = filas[0]
    print(
        f"\n== ESTA AL DIA? ultima creacion de pump.fun en Dune: {f0.get('ultimo_dato')}"
    )
    print(
        f"== MAYHEM EN TOTAL: {f0.get('mayhem_24h')} monedas en 24 h · "
        f"{round(_f(f0, 'mayhem_7d') / 7)} al dia de media en 7 dias"
    )
    activos = [f for f in filas if al_dia(f) > 0]
    print(
        f"== {len(filas)} devs con 2+ mayhem en 30 dias; {len(activos)} lanzaron "
        f"esta semana ({round(sum(al_dia(f) for f in activos))} lanzamientos/dia)"
    )
    print("\n== CORTES (ritmo de los ultimos 7 dias):")
    for p100 in (50, 30, 20, 10, 0):
        g = [f for f in ordena(activos) if _f(f, "pct_100k") >= p100]
        r = resumen(g)
        print(
            f"   llegan a 100k >= {p100:>2}%: {r['devs']:>4} devs · "
            f"{r['lanz_dia']:>6} lanz/dia · media 100k {r['pct_100k']}% · "
            f"30k {r['pct_30k']}%"
        )
    lista = elige(filas, a.objetivo)
    r = resumen(lista)
    print(
        f"\n== LISTA PARA ~{a.objetivo:.0f}/DIA: {r['devs']} devs · {r['lanz_dia']} "
        f"lanzamientos/dia · llegan a 100k {r['pct_100k']}% · a 30k {r['pct_30k']}%"
    )
    if r["lanz_dia"] < a.objetivo:
        print(
            f"   (no llega a {a.objetivo:.0f}: son todos los devs mayhem activos y "
            "limpios que hay)"
        )
    for f in lista[:15]:
        print(
            f"   {f['dev']}  7d {f.get('lanz_7d')} · 100k {f.get('pct_100k')}% · "
            f"30k {f.get('pct_30k')}% · mediana {_f(f, 'mediana_usd'):,.0f} $"
        )
    SALIDA.write_text(
        json.dumps(
            {
                "que_es": f"Devs mayhem de Dune para ~{a.objetivo:.0f} lanzamientos/dia",
                "sol": crudo.get("sol"),
                "resumen": r,
                "wallets": [f["dev"] for f in lista],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\nLista en {SALIDA}  (crudo en {CRUDO})")


if __name__ == "__main__":
    sys.exit(main())
