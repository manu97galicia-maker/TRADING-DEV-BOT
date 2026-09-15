"""Por cada moneda comprada: a cuanto entraste, hasta donde llego y a cuanto saliste.

Contesta "cuanto recorrido deje encima de la mesa". Con la entrada, el pico y
el precio medio de venta se ve de un vistazo si la escalera esta vendiendo
pronto o tarde.

OJO CON QUE ES EL PICO. Es el precio mas alto visto MIENTRAS el bot vigilaba
la posicion, no el maximo historico de la moneda. Lo que hiciera antes de
comprarla o despues de salir no se ve, porque nadie lo estaba mirando. Para
decidir sobre la escalera es justo el dato que sirve --lo de despues de salir
no se podia cobrar-- pero no es un ATH.

    uv run deploy/picos.py          # hoy
    uv run deploy/picos.py 3        # ultimos 3 dias
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REGISTRO = Path("trades") / "trades.log"
#: A partir de aqui se avisa de que la escalera vende pronto, en puntos
#: porcentuales del recorrido que se quedan sin cobrar.
_DEJADO_ALTO = 30


def _ops(dias: int) -> list[dict]:
    if not REGISTRO.is_file():
        return []
    corte = datetime.now(timezone.utc) - timedelta(days=dias)
    fuera = []
    for linea in REGISTRO.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(linea)
            cuando = datetime.fromisoformat(d["timestamp"]).replace(tzinfo=timezone.utc)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if cuando >= corte:
            fuera.append(d)
    return fuera


def main() -> None:
    """Tabla de entrada, pico y salida por moneda."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    ops = _ops(dias)
    if not ops:
        print(f"Sin operaciones en los ultimos {dias} dia(s).")
        return

    _tabla(*_juntar(ops), dias)


def _juntar(ops: list[dict]) -> tuple[dict, dict, dict, dict, dict, dict]:
    """Agrupa el registro por moneda: entrada, pico, ventas y motivos."""
    entrada: dict[str, float] = {}
    pico: dict[str, float] = {}
    origen: dict[str, str] = {}
    vendido_sol: dict[str, float] = defaultdict(float)
    vendido_tok: dict[str, float] = defaultdict(float)
    motivos: dict[str, list[str]] = defaultdict(list)

    for o in ops:
        sim = o.get("symbol") or o.get("token_address", "?")[:6]
        precio = float(o.get("price") or 0)
        cantidad = float(o.get("amount") or 0)
        if o.get("action") == "buy":
            entrada.setdefault(sim, precio)
            origen[sim] = o.get("origen") or "?"
        elif o.get("action") == "sell":
            vendido_sol[sim] += precio * cantidad
            vendido_tok[sim] += cantidad
            if o.get("motivo"):
                motivos[sim].append(str(o["motivo"]))
        if o.get("pico"):
            pico[sim] = max(float(o["pico"]), pico.get(sim, 0.0))

    return entrada, pico, origen, vendido_sol, vendido_tok, motivos


def _tabla(  # noqa: PLR0913
    entrada: dict,
    pico: dict,
    origen: dict,
    vendido_sol: dict,
    vendido_tok: dict,
    motivos: dict,
    dias: int,
) -> None:
    """Imprime la tabla y el aviso si se esta vendiendo pronto."""
    print(f"ENTRADA, PICO Y SALIDA · ultimos {dias} dia(s)\n")
    print(
        f"{'moneda':<10}{'fuente':<15}{'pico':>9}{'vendido a':>11}{'dejado':>9}  motivos"
    )
    print("-" * 78)

    dejados = []
    for sim in sorted(entrada, key=lambda s: -(pico.get(s, 0) / (entrada[s] or 1))):
        ent = entrada[sim]
        if ent <= 0:
            continue
        alto = pico.get(sim, 0.0)
        medio = (vendido_sol[sim] / vendido_tok[sim]) if vendido_tok[sim] else 0.0
        col_pico = f"x{alto / ent:.2f}" if alto else "?"
        col_vend = f"x{medio / ent:.2f}" if medio else "abierta"
        # Cuanto del recorrido se quedo sin cobrar.
        if alto and medio:
            resto = (alto - medio) / ent * 100
            col_dejado = f"{resto:+.0f}%"
            dejados.append(resto)
        else:
            col_dejado = "-"
        print(
            f"{sim:<10}{origen.get(sim, '?')[:14]:<15}{col_pico:>9}{col_vend:>11}"
            f"{col_dejado:>9}  {', '.join(motivos[sim][:4])}"
        )

    print("-" * 78)
    print(
        "\n  pico      = lo mas alto que llego MIENTRAS se vigilaba, sobre la entrada"
        "\n  vendido a = precio medio de todas sus ventas, sobre la entrada"
        "\n  dejado    = cuanto del recorrido no se cobro"
    )
    if dejados:
        media = sum(dejados) / len(dejados)
        print(f"\n  De media se deja sin cobrar un {media:+.0f}% del recorrido.")
        if media > _DEJADO_ALTO:
            print(
                "  Es mucho: la escalera esta vendiendo PRONTO. Mirar si conviene\n"
                "  subir los primeros peldanos o vender menos en ellos."
            )
    sin_pico = [s for s in entrada if not pico.get(s)]
    if sin_pico:
        print(
            f"\n  ({len(sin_pico)} sin pico: son de antes de que se empezara a "
            "apuntar, o siguen abiertas)"
        )


if __name__ == "__main__":
    main()
