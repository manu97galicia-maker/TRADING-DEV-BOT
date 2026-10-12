#!/usr/bin/env python3
"""Avisa por Telegram de que el bot no esta corriendo.

Por que existe
--------------
El 12-sep el bot llevaba ocho dias parado y no lo supimos hasta mirar a
mano. `Restart=always` cubre que el proceso reviente, pero NO cubre que
alguien lo pare, que se quede caido tras agotar los reintentos, ni que el
servicio no arranque tras un reinicio de la maquina. En ninguno de esos
casos llegaba nada.

Se usa de dos formas, que se complementan:

1. `OnFailure=` en pump-bot.service -> salta en cuanto el servicio entra en
   estado failed.
2. `pump-bot-vigia.timer` -> lo comprueba cada 5 minutos, que es lo que
   pilla el caso de "lo pararon a mano" y el de "no arranco al encender".

Manda el aviso UNA vez por caida: mientras siga parado no repite, para no
convertir el canal en una alarma que se acaba silenciando. Vuelve a armarse
solo cuando el bot vuelve a estar vivo.
"""

from __future__ import annotations

import os
import subprocess
import sys
from html import escape
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

SERVICIO = os.getenv("PUMP_BOT_SERVICE", "pump-bot")
#: Marca de "ya avise de esta caida". Se borra cuando el bot vuelve.
TESTIGO = Path("/tmp/pump-bot-caida-avisada")  # noqa: S108


def avisos_apagados() -> bool:
    """True si los bots encendidos piden `avisos_telegram: false`.

    POR QUE EXISTE, pedido el 11-oct: "no envies mas notificaciones por
    telegram". El bot ya estaba callado --`bot_runner.py` llama a
    `configurar_avisos(cfg["avisos_telegram"])` y eso pone `_SILENCIO`--
    pero ESTE script es otro proceso, lanzado por `pump-bot-vigia`, y no
    pasaba por ahi. Resultado: con todo "apagado" seguian llegando avisos
    cada vez que el bot se reiniciaba, y el 11-oct se reinicio media docena
    de veces.

    O SEA QUE `avisos_telegram` MENTIA: decia apagar los avisos y solo
    apagaba los de un proceso. Ahora es el interruptor de los dos.

    SI NO SE PUEDE LEER LA CONFIGURACION, SE AVISA IGUAL. Este script es el
    unico que cuenta que el bot esta muerto; ante la duda tiene que sonar,
    no callarse. Callarse por un yaml ilegible seria el fallo que costo
    ocho dias en septiembre, otra vez.
    """
    try:
        import yaml  # noqa: PLC0415

        encendidos = []
        for fichero in sorted((RAIZ / "bots").glob("*.yaml")):
            try:
                cfg = yaml.safe_load(fichero.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                return False  # ilegible: que suene
            if cfg.get("enabled", True):
                # `is False` Y NO `bool(...)`, que es como lo hace el bot:
                # `configurar_avisos` tiene `_SILENCIO = solo is False`. Los
                # tres valores de esta clave significan cosas distintas:
                #   nada   -> se manda TODO (nadie ha pedido silencio)
                #   false  -> silencio total
                #   lista  -> solo esos tipos; el aviso de caida va por
                #             `notify_ahora`, que se salta ese filtro aposta
                # Con `bool(...)` la ausencia contaba como silencio y el
                # vigia se habria callado sin que nadie se lo pidiera. Lo
                # caza `test_sin_la_clave_se_avisa`.
                encendidos.append(cfg.get("avisos_telegram") is False)
        if not encendidos:
            return False  # ningun bot encendido: que suene
        return all(encendidos)
    except Exception:  # noqa: BLE001 - ante cualquier duda, que suene
        return False


def estado(servicio: str) -> str:
    """Devuelve el estado de systemd: active, failed, inactive..."""
    try:
        salida = subprocess.run(  # noqa: S603
            ["/usr/bin/systemctl", "is-active", servicio],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "desconocido"
    return salida.stdout.strip() or "desconocido"


def ultimas_lineas(servicio: str, n: int = 12) -> str:
    """Ultimas lineas del diario, para no tener que entrar a mirarlas."""
    try:
        salida = subprocess.run(  # noqa: S603
            ["/usr/bin/journalctl", "-u", servicio, "-n", str(n), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return salida.stdout.strip()


def construir_aviso(servicio: str, situacion: str, registro: str) -> str:
    """Arma el mensaje: que pasa, y las ultimas lineas del log."""
    cabecera = (
        "🛑 <b>EL BOT ESTA PARADO</b>\n"
        f"Servicio <code>{escape(servicio)}</code>: <b>{escape(situacion)}</b>\n"
        "No esta vigilando ni puede vender lo que tenga abierto."
    )
    if not registro:
        return cabecera
    cola = escape(registro[-1200:])
    return f"{cabecera}\n\n<pre>{cola}</pre>"


def main() -> int:
    """Comprueba el servicio y avisa si no esta vivo."""
    # Diferidos a proposito: hay que cargar el .env ANTES de importar el
    # notificador, que lee las variables de entorno al mandar.
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(RAIZ / ".env")
    from monitoring.notifier import notify_ahora  # noqa: PLC0415

    if avisos_apagados():
        # SE DICE EN VOZ ALTA por journalctl. Un silencio silencioso es como
        # se pierden los avisos sin que nadie lo sepa.
        print(
            "Vigia callado: los bots encendidos piden `avisos_telegram: false`. "
            "Si el bot se cae, NADIE te lo va a decir.",
            file=sys.stderr,
        )
        return 0

    situacion = estado(SERVICIO)
    if situacion == "active":
        # Volvio: se rearma el aviso para la proxima caida.
        TESTIGO.unlink(missing_ok=True)
        return 0
    if TESTIGO.exists():
        # Ya se aviso de esta caida. Repetir cada 5 minutos convierte la
        # alarma en ruido y se acaba ignorando.
        return 0
    mensaje = construir_aviso(SERVICIO, situacion, ultimas_lineas(SERVICIO))
    if notify_ahora(mensaje):
        TESTIGO.touch()
    else:
        print(  # lo lee journalctl cuando falta la config
            "Sin TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID en el .env: "
            "no se puede avisar de la caida.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
