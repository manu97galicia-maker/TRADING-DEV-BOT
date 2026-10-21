"""Carga del YAML del bot BSC y las TRES PUERTAS que separan papel de real.

`INSTRUCCIONES.md` lo dice asi: "Hay tres puertas independientes: los dos
booleanos del YAML, la frase exacta del entorno y una clave BSC valida. Si
cualquiera falla, no se firma."

POR QUE TRES Y NO UNA. Una sola bandera se enciende sin querer: copiando un
fichero, editando a ojo desde el movil, o con un `sed` mal puesto. Tres
puertas en SITIOS DISTINTOS --el YAML versionado, el `.env` que no se versiona
y la wallet-- no se cruzan por accidente.

ESTE MODULO NO IMPRIME NUNCA UN SECRETO. Dice si una variable existe y si
tiene la forma correcta; su valor no sale de aqui ni en un error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: La frase exacta que `INSTRUCCIONES.md` exige en BSC_ENABLE_LIVE_TRADING.
#: Se compara entera y sin normalizar: si se aceptara "i_understand..." en
#: minusculas o con espacios, dejaria de ser una puerta y pasaria a ser una
#: sugerencia.
FRASE_LIVE = "I_UNDERSTAND_BSC_LIVE_TRADING"

#: Chain ID de BNB Smart Chain. Un RPC que conteste otro numero es otro sitio.
CHAIN_ID_BSC = 56

#: Una clave privada de 32 bytes son 64 caracteres hexadecimales.
_LARGO_CLAVE_HEX = 64


class ConfigInvalidaError(ValueError):
    """La configuracion no se puede usar. El mensaje nunca lleva secretos."""


@dataclass(frozen=True)
class Estrategia:
    """Los numeros de `INSTRUCCIONES.md`, ya validados."""

    compra_usd: float = 25.0
    max_entry_mc_usd: float = 9_000.0
    min_migraciones_dev: int = 2
    min_ratio_migrado: float = 0.08
    max_impuesto_total: float = 0.06
    horas_nombre_repetido: float = 2.0
    max_posiciones: int = 2
    reserva_bnb_usd: float = 5.0
    max_gas_usd: float = 1.30
    stop_loss: float = 0.38


@dataclass(frozen=True)
class ConfigBSC:
    """Configuracion completa de una ejecucion."""

    ruta: Path
    paper_trade: bool
    live_transactions: bool
    estrategia: Estrategia
    rutas_estado: dict[str, Path] = field(default_factory=dict)

    @property
    def es_real(self) -> bool:
        """True solo si las DOS banderas del YAML piden real.

        Se exigen las dos a proposito: `paper_trade: false` por si solo no
        basta, porque es la que mas facil se queda a medias al copiar la
        plantilla.
        """
        return (not self.paper_trade) and self.live_transactions


def cargar(ruta: str | Path) -> ConfigBSC:
    """Lee y valida el YAML. No mira el entorno ni la wallet todavia."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ConfigInvalidaError(f"no existe el fichero de configuracion: {ruta}")
    datos: Any = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    if not isinstance(datos, dict):
        raise ConfigInvalidaError(f"{ruta} no contiene un mapa YAML")

    modo = datos.get("mode") or {}
    if not isinstance(modo, dict):
        raise ConfigInvalidaError("el bloque `mode` tiene que ser un mapa")
    # POR DEFECTO, PAPEL. Un YAML al que le falte `mode` no puede acabar
    # comprando: la ausencia de una bandera nunca significa "si".
    paper = bool(modo.get("paper_trade", True))
    live = bool(modo.get("live_transactions", False))

    est = datos.get("strategy") or {}
    if not isinstance(est, dict):
        raise ConfigInvalidaError("el bloque `strategy` tiene que ser un mapa")
    estrategia = Estrategia(
        compra_usd=float(est.get("compra_usd", 25.0)),
        max_entry_mc_usd=float(est.get("max_entry_mc_usd", 9_000.0)),
        min_migraciones_dev=int(est.get("min_migraciones_dev", 2)),
        min_ratio_migrado=float(est.get("min_ratio_migrado", 0.08)),
        max_impuesto_total=float(est.get("max_impuesto_total", 0.06)),
        horas_nombre_repetido=float(est.get("horas_nombre_repetido", 2.0)),
        max_posiciones=int(est.get("max_posiciones", 2)),
        reserva_bnb_usd=float(est.get("reserva_bnb_usd", 5.0)),
        max_gas_usd=float(est.get("max_gas_usd", 1.30)),
        stop_loss=float(est.get("stop_loss", 0.38)),
    )
    _validar_estrategia(estrategia)

    rutas = {k: Path(v) for k, v in (datos.get("state") or {}).items()}
    return ConfigBSC(ruta, paper, live, estrategia, rutas)


def _validar_estrategia(e: Estrategia) -> None:
    """Rechaza numeros imposibles ANTES de que lleguen a la cadena."""
    if e.compra_usd <= 0:
        raise ConfigInvalidaError("compra_usd tiene que ser mayor que 0")
    if e.max_entry_mc_usd <= 0:
        raise ConfigInvalidaError("max_entry_mc_usd tiene que ser mayor que 0")
    if not 0 < e.stop_loss < 1:
        raise ConfigInvalidaError("stop_loss tiene que estar entre 0 y 1 (0,38 = -38%)")
    if not 0 <= e.max_impuesto_total < 1:
        raise ConfigInvalidaError("max_impuesto_total tiene que estar entre 0 y 1")
    if e.max_posiciones < 1:
        raise ConfigInvalidaError("max_posiciones tiene que ser 1 o mas")
    if e.reserva_bnb_usd < 0 or e.max_gas_usd < 0:
        raise ConfigInvalidaError("la reserva y el gas no pueden ser negativos")


def puertas_abiertas(cfg: ConfigBSC, entorno: dict[str, str] | None = None) -> tuple[bool, list[str]]:
    """Comprueba las tres puertas. Devuelve (se_puede_firmar, lo_que_falta).

    NUNCA devuelve el valor de un secreto: solo si esta, si falta o si no
    tiene la forma esperada.
    """
    env = entorno if entorno is not None else dict(os.environ)
    faltan: list[str] = []

    if not cfg.es_real:
        faltan.append(
            "el YAML esta en papel (`paper_trade: false` Y "
            "`live_transactions: true` son necesarios para real)"
        )

    frase = env.get("BSC_ENABLE_LIVE_TRADING", "")
    if frase != FRASE_LIVE:
        faltan.append(
            "BSC_ENABLE_LIVE_TRADING no contiene la frase exacta que pide "
            "INSTRUCCIONES.md"
        )

    if not _clave_con_forma_valida(env.get("BSC_PRIVATE_KEY", "")):
        faltan.append(
            "BSC_PRIVATE_KEY ausente o sin la forma de una clave hexadecimal "
            "de 32 bytes"
        )

    return (not faltan), faltan


def _clave_con_forma_valida(clave: str) -> bool:
    """Solo mira la FORMA. No deriva la direccion ni toca la clave.

    Una clave de Web3.py son 32 bytes en hexadecimal, con o sin `0x`. Esto
    descarta el error mas comun y mas caro: pegar una seed phrase, que
    `INSTRUCCIONES.md` prohibe expresamente.
    """
    c = clave.strip()
    if not c:
        return False
    # EL CHEQUEO DE ESPACIOS ES REDUNDANTE y se deja a proposito: el filtro
    # hexadecimal de abajo ya tumba cualquier seed phrase, porque palabras
    # como "abandon" llevan letras que no son hex. Lo comprobo una mutacion
    # (21-oct): quitar esta linea no rompia ningun test. Se queda porque
    # rechaza el error mas caro --pegar una seed phrase, que
    # INSTRUCCIONES.md prohibe-- de forma legible y temprana, no porque sea
    # lo unico que lo impide.
    if " " in c:
        return False
    c = c[2:] if c.lower().startswith("0x") else c
    if len(c) != _LARGO_CLAVE_HEX:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in c)
