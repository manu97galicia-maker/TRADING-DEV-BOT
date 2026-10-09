"""Tests de la resolucion de canales de Telegram.

El riesgo que cubren: Telethon revienta al arrancar si UNO de los nombres
de ``chats=`` no se puede resolver, y eso se llevaba por delante la fuente
entera. Anadir MoonTok al lado de Nitro hacia ese fallo mas probable.
"""

# ruff: noqa: S101

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import yaml

from monitoring.telegram_watcher import resolve_channels


def _config() -> dict:
    return yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
        ).read_text(encoding="utf-8")
    )


def _resolver(validos: set[str]) -> Callable[[str | int], Awaitable[object]]:
    """Devuelve un resolvedor falso que imita a Telethon: o entidad o error."""

    async def resolver(canal: str | int) -> object:
        if canal not in validos:
            msg = f"Cannot find any entity corresponding to {canal!r}"
            raise ValueError(msg)
        return f"entidad:{canal}"

    return resolver


def test_un_canal_malo_no_se_lleva_por_delante_a_los_buenos() -> None:
    """Es el caso real: MoonTok mal escrito no debe callar a Nitro."""
    resueltos, fallidos = asyncio.run(
        resolve_channels(
            _resolver({"dextools_nitro"}), ["dextools_nitro", "canal_inventado"]
        )
    )
    assert resueltos == ["entidad:dextools_nitro"]
    assert fallidos == ["canal_inventado"]


def test_todos_buenos_se_resuelven_todos() -> None:
    resueltos, fallidos = asyncio.run(
        resolve_channels(
            _resolver({"dextools_nitro", "cryptolistings"}),
            ["dextools_nitro", "cryptolistings"],
        )
    )
    assert resueltos == ["entidad:dextools_nitro", "entidad:cryptolistings"]
    assert fallidos == []


def test_todos_malos_no_devuelve_ninguno() -> None:
    """Quien llama usa esta lista vacia para NO caer en 'todos los chats'."""
    resueltos, fallidos = asyncio.run(
        resolve_channels(_resolver(set()), ["uno", "otro"])
    )
    assert resueltos == []
    assert fallidos == ["uno", "otro"]


def test_sin_canales_configurados_no_resuelve_nada() -> None:
    """None significa 'todos los chats', y eso lo decide quien llama."""
    assert asyncio.run(resolve_channels(_resolver(set()), None)) == ([], [])
    assert asyncio.run(resolve_channels(_resolver(set()), [])) == ([], [])


def test_una_entidad_nula_cuenta_como_fallo() -> None:
    """Telethon puede devolver None en vez de lanzar; es igual de inservible."""

    async def resolver(_canal: str | int) -> object:
        return None

    resueltos, fallidos = asyncio.run(resolve_channels(resolver, ["x"]))
    assert resueltos == []
    assert fallidos == ["x"]


def test_los_canales_configurados_son_los_pedidos() -> None:
    """Cada canal de esta lista es una fuente de compra con dinero real, asi
    que anadir o quitar uno se ve aqui en rojo.

    Van mezclados dos tipos y conviene saberlo al leer resultados:
      - Wagbocooks, LegendsAlphas: son CALLS, alguien opina sin pagar.

    EL 05-oct SE QUEDARON SOLO ESOS DOS. Se fueron los de PAGO
    (dextools_nitro, cryptolistings) y SolanaInsiderTerminal, en el commit
    "Restrict paper strategy to Telegram alpha channels". La fuente entera
    esta apagada desde el 09-oct, pero la lista se fija igual: si vuelve,
    tiene que volver con los canales que se acordaron.

    Desde el 14-sep cada compra apunta de que canal vino, asi que
    `deploy/resumen_dia.py` los separa y se puede ver cual gana.
    """
    canales = _config()["filters"]["telegram_calls"]["channels"]
    assert canales == ["Wagbocooks", "LegendsAlphas"]


def test_el_importe_de_canal_coincide_con_el_global() -> None:
    """EL FALLO QUE YA PASO UNA VEZ.

    El importe de la SENAL pisa al `buy_amount` global, asi que si aqui hubiera
    un numero distinto, el de arriba no serviria de nada y se estaria comprando
    con un tamano que nadie puso a proposito. Pedido el 21-sep: 0,04 SOL en las
    compras de canal, igual que la entrada de titulares.
    """
    cfg = _config()
    tg = cfg["filters"]["telegram_calls"]
    global_ = cfg["trade"]["buy_amount"]
    for clave in ("normal_buy_amount", "nitro_buy_amount", "promoted_buy_amount"):
        assert tg[clave] == global_, f"{clave} no coincide con el buy_amount global"
    assert cfg["filters"]["holders"]["buy_amount"] == global_
    # Desde el 23-sep los cuatro estan a CERO porque manda el importe en
    # dolares. Que coincidan sigue importando: el dia que se apague ese modo,
    # los cuatro tienen que volver a la vez o el de la senal pisara al global.
    _t = cfg["trade"]
    _encendidos = [
        k
        for k in ("buy_amount_usd", "fraccion_saldo", "buy_amount")
        if _t.get(k, 0) > 0
    ]
    assert len(_encendidos) == 1, (
        f"tiene que mandar UNO de los tres modos, y mandan: "
        f"{_encendidos or 'ninguno: el bot no compraria nada'}"
    )


def test_los_canales_estan_apagados_y_los_avisos_tambien() -> None:
    """Dos cosas distintas con el mismo nombre, y es facil deshacer una al
    tocar la otra.

    Los CANALES son de donde el bot lee senales de compra: apagados el 24-sep
    ("solo esta estrategia", la curva). Los AVISOS son los mensajes que el bot
    te manda, y llevan apagados desde antes por otro motivo distinto: "no
    quiero avisos, solo quiero que se compre y se venda".

    QUE ESTEN LOS DOS EN FALSE NO SIGNIFICA QUE SEAN LO MISMO. El dia que se
    vuelvan a encender los canales, los avisos tienen que seguir apagados --
    y al reves. Por eso se comprueban juntos y por separado.

    El resto de la configuracion de Telegram --los cinco canales, los
    importes, los sufijos-- sigue entera en el yaml: volver a encenderla es
    cambiar un false.
    """
    cfg = _config()
    assert cfg["filters"]["telegram_calls"]["enabled"] is False
    assert cfg["avisos_telegram"] is False
    # Y la configuracion no se ha borrado al apagarla.
    assert len(cfg["filters"]["telegram_calls"]["channels"]) >= 1


def test_solo_monedas_de_pumpfun() -> None:
    """Pedido, y repetido: "acuerdate, solo moneda de pump.fun normal"."""
    assert _config()["filters"]["telegram_calls"]["suffixes"] == ["pump"]
