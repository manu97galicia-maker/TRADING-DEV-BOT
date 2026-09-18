"""Revision completa del droplet: ¿esta listo para operar en REAL?

Un solo comando que mira todo lo que decide si el bot compra de verdad, y da
un veredicto. Existe porque estos fallos no se ven de uno en uno: la clave
estaba en el fichero equivocado, o el YAML dice papel, o la rama es otra, y
cada cosa por separado parecia correcta.

NO imprime ningun secreto: de las claves solo dice si estan o no.

    uv run deploy/revisar_droplet.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

CONFIG = Path("bots") / "bot-paper-ai-entry.yaml"
#: Claves que hacen falta, y para que.
CLAVES = {
    "SOLANA_PRIVATE_KEY": "la wallet: sin esto no se compra nada",
    "SOLANA_NODE_RPC_ENDPOINT": "el nodo",
    "SOLANA_NODE_WSS_ENDPOINT": "el nodo en directo (curva al 95%)",
    "DEXTOOLS_API_KEY": "fuente DEXTools",
    "TELEGRAM_API_ID": "fuente Telegram",
    "TELEGRAM_API_HASH": "fuente Telegram",
}

_ok, _mal, _avisos = [], [], []


def bien(t: str) -> None:
    _ok.append(t)
    print(f"  OK    {t}")


def mal(t: str) -> None:
    _mal.append(t)
    print(f"  FALLO {t}")


def aviso(t: str) -> None:
    _avisos.append(t)
    print(f"  AVISO {t}")


def _rama() -> None:
    print("\n== RAMA ==")
    try:
        r = subprocess.run(
            ["/usr/bin/git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        aviso("no pude leer la rama")
        return
    if r == "main":
        mal(
            f"estas en '{r}': esa es la version PAPEL de Codex, sin curva 95% "
            "ni el arreglo de retomar posiciones"
        )
    elif r:
        bien(f"rama '{r}'")


def _fichero_env() -> Path | None:
    """El que cargaria el bot, con la MISMA regla que config_loader."""
    print("\n== FICHERO .env ==")
    try:
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        mal(f"no pude leer {CONFIG}")
        return None
    nombre = cfg.get("env_file")
    if not nombre:
        aviso("el YAML no declara env_file")
        return None
    junto = CONFIG.parent / nombre
    elegido = junto if junto.is_file() else Path(nombre)
    print(f"  el YAML pide: {nombre}")
    if junto.is_file():
        aviso(f"manda {junto} (tiene prioridad sobre ./{nombre})")
    if not elegido.is_file():
        mal(f"{elegido} NO EXISTE: el bot no lee ninguna clave")
        return None
    bien(f"lee {elegido}")

    sueltos = [p.name for p in Path().glob("*.env") if p.name != elegido.name]
    if sueltos:
        aviso(f"hay otros ficheros de claves que el bot NO lee: {', '.join(sueltos)}")
    return elegido


def _claves(env: Path) -> None:
    print("\n== CLAVES ==")
    try:
        texto = env.read_text(encoding="utf-8")
    except OSError:
        mal(f"no pude leer {env}")
        return
    puestas = {
        linea.split("=", 1)[0].strip()
        for linea in texto.splitlines()
        if "=" in linea
        and not linea.strip().startswith("#")
        and linea.split("=", 1)[1].strip()
    }
    for clave, para in CLAVES.items():
        if clave in puestas:
            bien(f"{clave} ({para})")
        elif clave.startswith(("DEXTOOLS", "TELEGRAM")):
            aviso(f"{clave} sin poner -> {para} no arranca")
        else:
            mal(f"{clave} sin poner -> {para}")
    _nodo(texto)


def _nodo(texto: str) -> None:
    """Que nodo se esta usando. Se imprime el HOST, nunca la clave.

    Importa para el stop loss y los take profit: cada posicion abierta pide el
    precio UNA VEZ POR SEGUNDO. Con el endpoint publico y gratuito de Solana
    eso son 429 a los pocos segundos, y cuando no hay precio la escalera no
    puede dispararse -- a los 40 segundos seguidos sin precio el bot suelta la
    posicion. O sea: un nodo malo se parece exactamente a "los take profit no
    saltan".
    """
    for clave in ("SOLANA_NODE_RPC_ENDPOINT", "SOLANA_NODE_WSS_ENDPOINT"):
        valor = ""
        for linea in texto.splitlines():
            if linea.strip().startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            if k.strip() == clave:
                valor = v.strip()
        if not valor:
            continue
        m = re.match(r"\w+://([^/?]+)", valor)
        host = m.group(1) if m else "?"
        # La clave de API viaja en la ruta o en el parametro; el host no la lleva.
        if "mainnet-beta.solana.com" in host:
            mal(
                f"{clave} = {host}: es el nodo PUBLICO gratuito. Limita a pocas "
                "peticiones y el precio se corta -> la escalera no dispara"
            )
        elif "helius" in host.lower():
            bien(f"{clave} = {host} (Helius)")
        else:
            aviso(f"{clave} = {host} (nodo propio o de otro proveedor)")


#: Fuentes de adquisicion. Si hay alguna encendida, el listener principal
#: NO llega a arrancar: `start` lanza las fuentes y hace `return` sin tocarlo.
FUENTES = (
    "buy_on_dex_paid",
    "birdeye_trending",
    "dextools_hotpools",
    "curva_96",
    "telegram_calls",
    "discord_calls",
    "gmgn_signals",
    "presion",
)


def _listener(cfg: dict, texto_env: str) -> None:
    """Que escucha el bot, y si el `listener_type` configurado sirve de algo.

    LA TRAMPA QUE ESTO DESTAPA. `listener_type` (logs / blocks / geyser /
    pumpportal) solo manda cuando el bot compra por el camino normal: ver
    nacer una moneda y decidir. Pero en cuanto hay UNA fuente de adquisicion
    encendida, el arranque lanza las fuentes y se va sin montar ese listener.
    Cada fuente trae su propia forma de escuchar.

    O sea: con fuentes activas, cambiar `listener_type` a geyser no acelera
    NADA -- y si falta la configuracion de geyser, antes ni siquiera
    arrancaba el bot. Vale la pena verlo escrito antes de pagar por un
    endpoint que no se va a usar.
    """
    print("\n== QUE ESCUCHA ==")
    f = cfg.get("filters", {})
    tipo = str(f.get("listener_type") or "?")
    activas = [
        n for n in FUENTES if ((f.get(n) or {}).get("enabled"))
    ]
    hay_geyser = all(
        any(
            linea.split("=", 1)[0].strip() == clave
            and linea.split("=", 1)[1].strip()
            for linea in texto_env.splitlines()
            if "=" in linea and not linea.strip().startswith("#")
        )
        for clave in ("GEYSER_ENDPOINT", "GEYSER_API_TOKEN")
    )

    if activas:
        bien(f"fuentes de compra activas: {', '.join(activas)}")
        aviso(
            f"listener_type '{tipo}' NO se usa: con fuentes activas el "
            "listener principal no arranca. Cambiarlo a geyser no cambia nada"
        )
    else:
        bien(f"sin fuentes de adquisicion: manda listener_type '{tipo}'")
        if tipo == "geyser" and not hay_geyser:
            mal(
                "listener_type geyser sin GEYSER_ENDPOINT/GEYSER_API_TOKEN: "
                "el bot cae a 'logs' y avisa en el log"
            )

    if hay_geyser:
        bien("GEYSER_ENDPOINT y GEYSER_API_TOKEN puestos")
    else:
        aviso(
            "sin GEYSER_ENDPOINT/GEYSER_API_TOKEN en el .env: geyser no esta "
            "disponible (no todos los planes de nodo lo incluyen)"
        )


def _autoactualiza() -> None:
    """Si el droplet se trae los cambios solo, y cuando lo hizo por ultima vez.

    UN AUTOMATISMO QUE NADIE COMPRUEBA ES UN AUTOMATISMO CAIDO. En agosto el
    bot estuvo OCHO DIAS parado sin que nadie se enterara. Con el temporizador
    pasa igual pero peor: mientras parece que todo llega solo, el bot puede
    llevar semanas corriendo codigo viejo.
    """
    print("\n== SE ACTUALIZA SOLO? ==")
    try:
        activo = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "pump-bot-autoactualiza.timer"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        aviso("no pude preguntar por el temporizador")
        return

    if activo != "active":
        aviso(
            "el temporizador NO esta activo: los cambios de GitHub no llegan "
            "solos. Se enciende con: bash /root/ai-bot/deploy/arranca.sh"
        )
        return
    bien("temporizador activo: los cambios llegan solos cada 10 min")

    registro = Path("logs") / "autoactualiza.log"
    if not registro.exists():
        aviso("todavia no ha corrido ninguna vez")
        return
    try:
        lineas = [x for x in registro.read_text(encoding="utf-8").splitlines() if x]
    except OSError:
        return
    ultima = next(
        (x for x in reversed(lineas) if x.startswith("Actualizado:")), ""
    )
    if ultima:
        bien(f"ultimo cambio traido: {ultima.removeprefix('Actualizado: ')}")
    elif any(x.startswith("PULL FALLIDO") for x in lineas[-20:]):
        mal("el ultimo intento de traer cambios FALLO: mirar logs/autoactualiza.log")
    else:
        bien("corriendo, sin cambios nuevos que traer")


def _secretos_sueltos() -> None:
    """Ficheros que NO pueden acabar en GitHub, y basura que estorba.

    LA SESION DE TELEGRAM ES UNA LLAVE. No es un fichero de datos: quien la
    tenga entra en la cuenta sin contrasena ni SMS, y ademas lee los canales
    de senales. Si esta sin ignorar, un `git add -A` hecho con prisa desde el
    droplet la sube. El .env ya se colo asi una vez y sigue en el historial.

    Y LAS COPIAS DEL TRADER estorban de otra forma: ninguna corre, pero estan
    a un `uv run` de mover dinero con codigo de hace meses, y son las que
    hacian que `git status` tardara cuatro segundos.
    """
    print("\n== FICHEROS SUELTOS ==")
    sesiones = sorted(Path().glob("*.session"))
    if not sesiones:
        bien("sin sesiones de Telegram sueltas en el directorio")
    else:
        for ruta in sesiones:
            if _esta_ignorado(ruta.name):
                bien(f"{ruta.name} esta ignorado por git (no puede subirse)")
            else:
                mal(
                    f"{ruta.name} NO esta en .gitignore: es una llave de la "
                    "cuenta de Telegram y un `git add -A` la subiria"
                )

    copias = sorted(Path().glob("*_new.py")) + sorted(Path().glob("*.new.py"))
    if copias:
        aviso(
            f"{len(copias)} copias viejas del trader en el directorio "
            f"({copias[0].name}...): no corren, pero se pueden lanzar a mano"
        )


def _esta_ignorado(nombre: str) -> bool:
    """True si git ignora ese fichero. Ante la duda, False (avisa de mas)."""
    try:
        return (
            # S603 silenciado como en la otra llamada de este fichero: el
            # nombre sale de listar el directorio, no de nadie de fuera, y
            # `check-ignore` no ejecuta nada.
            subprocess.run(  # noqa: S603
                ["/usr/bin/git", "check-ignore", "-q", nombre],
                capture_output=True,
                timeout=10,
                check=False,
            ).returncode
            == 0
        )
    except Exception:  # noqa: BLE001
        return False


def _modo(cfg: dict) -> None:
    print("\n== MODO ==")
    t, f = cfg.get("trade", {}), cfg.get("filters", {})
    if t.get("pausar_compras"):
        # No es un fallo: es lo que se ha pedido. Pero tiene que verse a la
        # primera, porque un bot en pausa que nadie recuerda haber pausado
        # parece un bot roto.
        aviso(
            "MODO ESTUDIO (pausar_compras: true): NO se compra nada nuevo. "
            "Lo ya abierto se sigue vigilando y vendiendo con normalidad. "
            "Para volver a comprar, poner pausar_compras: false"
        )
    papel = t.get("paper_trade", f.get("paper_trade"))
    if papel:
        mal("paper_trade esta en true: SIMULA, no compra de verdad")
    elif t.get("pausar_compras"):
        bien("paper_trade false: las VENTAS son de verdad (lo que importa en pausa)")
    else:
        bien("paper_trade false: compra de VERDAD")
    tope = int(t.get("max_real_buys") or 0)
    if tope:
        aviso(f"max_real_buys = {tope}: se para tras {tope} compras por tanda")
    else:
        bien("max_real_buys 0: sin tope de compras")


def _estrategia(cfg: dict) -> None:
    print("\n== ESTRATEGIA ==")
    t, f = cfg["trade"], cfg["filters"]
    print(
        f"  compra {t['buy_amount']} SOL · tope {t['tope_compra_sol']} · "
        f"max {t['max_posiciones']} posiciones · reserva {t['min_reserve_sol']}"
    )
    print(
        f"  TP1 +{t['take_profit_percentage'] * 100:.0f}% vende "
        f"{t['take_profit_sell_percentage'] * 100:.0f}% · "
        f"stop -{t['stop_loss_percentage'] * 100:.0f}%"
        f"{' (vende TODO)' if t.get('vender_todo_en_stop') else ''}"
    )
    activas = [
        k
        for k, v in f.items()
        if isinstance(v, dict) and v.get("enabled") and k != "dexscreener"
    ]
    print(f"  fuentes encendidas: {', '.join(activas)}")
    if "curva_96" in f and f["curva_96"].get("enabled"):
        bien(f"curva al {f['curva_96']['umbral'] * 100:.0f}% activa")
    else:
        aviso("la fuente de la curva no esta activa")


def _servicio() -> None:
    print("\n== SERVICIO ==")
    for arg, texto in (
        ("is-active", "corriendo"),
        ("is-enabled", "arranca solo al reiniciar el servidor"),
    ):
        try:
            # Ruta absoluta y argumentos fijos: no hay entrada del usuario.
            r = subprocess.run(  # noqa: S603
                ["/usr/bin/systemctl", arg, "pump-bot"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            aviso(f"no pude comprobar {arg}")
            continue
        if r in ("active", "enabled"):
            bien(f"{texto} ({r})")
        else:
            (mal if arg == "is-active" else aviso)(f"NO {texto} ({r or '?'})")


def main() -> None:
    """Revisa el droplet entero y da un veredicto."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 58)
    print("REVISION DEL DROPLET")
    print("=" * 58)

    _rama()
    env = _fichero_env()
    if env:
        _claves(env)
    try:
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        _listener(cfg, env.read_text(encoding="utf-8") if env else "")
        _autoactualiza()
        _secretos_sueltos()
        _modo(cfg)
        _estrategia(cfg)
    except (OSError, yaml.YAMLError, KeyError) as exc:
        mal(f"no pude leer la configuracion: {exc}")
    _servicio()

    print("\n" + "=" * 58)
    if _mal:
        print(f"NO ESTA LISTO PARA REAL. {len(_mal)} fallo(s):")
        for x in _mal:
            print(f"  - {x}")
    else:
        print("LISTO PARA OPERAR EN REAL.")
    if _avisos:
        print(f"\n{len(_avisos)} aviso(s), no impiden operar:")
        for x in _avisos:
            print(f"  - {x}")
    print("=" * 58)


if __name__ == "__main__":
    main()
