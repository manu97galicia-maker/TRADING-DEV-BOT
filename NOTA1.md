# Nota 1 — cambios sobre la estrategia de señales

Para quien retome el trabajo (Codex incluido). Rama:
`claude/private-key-env-access-cc1er2`, 8 commits sobre `main` (`30d0661`).

`AGENTS.md` sigue mandando: probar con `learning-examples/` antes de tocar el
bot, nada de fondos reales en sesiones de desarrollo, y `ruff format` +
`ruff check` antes de dar nada por cerrado.

---

## 1. Solo corre una estrategia

`bot_runner.py:440` recorre **todo** `bots/*.yaml` y arranca cada uno con
`enabled: true`. Había **8 a la vez**, y 5 compraban con SOL real sobre la
misma wallet (`yolo14k` con `max_real_buys: 999`).

Ahora la única con `enabled: true` es **`bot-paper-ai-entry`**, y está
encendida para que la simulación acumule muestra. Sus cerrojos de dinero
siguen intactos: `paper_trade: true` y `max_real_buys: 0`.

> Reactivar cualquier otro YAML lo pone a operar en paralelo compartiendo
> wallet. Es el fallo fácil de repetir.

## 2. Entrada: fuera el filtro de edad, dentro la velocidad

- `max_token_age_minutes: 0` — la edad de la moneda ya no filtra. Una moneda
  vieja que acaba de recibir un pago o un boost sigue trayendo volumen, y esa
  es la señal. La frescura **del pago** sí se sigue exigiendo
  (`max_age_minutes: 10`).
- `max_token_age: 120` (antes 10800) — es antigüedad **en la cola interna**,
  no de la moneda; con entrada instantánea, una señal parada 3 h no vale nada.
- `fixed_amount: 1000000` ≈ 0,0001 SOL ≈ 0,02 $ a 200 $/SOL. La fórmula para
  recalcularlo está en el propio YAML.

**El cuello de botella real de la latencia no era `poll_interval`.**
`dexpaid_feed.get_candidates` llamaba a `get_orders` por **cada** candidato, y
ese endpoint va por el limitador de 60 rpm (1 req/s). Con ~30 candidatos por
ciclo eso son ~30 s de espera obligatoria: bajar el sondeo no servía de nada.

Un boost **es prueba de pago en sí mismo**, así que esa llamada ahora solo se
hace en la ruta de pagos (`_paid_age_minutes`). Consecuencia aceptada: los
candidatos de boost ya no traen `payment_age_min`, y en el panel "Pagaron Dex"
salen con ese campo vacío.

## 3. Birdeye: implementado y DESACTIVADO a propósito

`src/monitoring/birdeye_source.py` dispara cuando una moneda **entra** en la
lista de tendencias (`GET /defi/token_trending`), no por estar en ella. La foto
del arranque se registra sin comprar, igual que hace la fuente de dex-paid.

Viene con `enabled: false`, y el motivo importa:

- Tendencia mide **atención**, no gasto. No es el equivalente al paid de
  DexScreener ni al Nitro de DEXTools.
- Lo que Birdeye cobra es su **Token Info Update Service** (200–300 $,
  verificación en **2–7 días hábiles**) y **no expone ningún endpoint de "quién
  acaba de pagar"**. Días de retraso no sirven para una moneda de pump.fun.
- Además la mayoría de las tendencias ya migraron fuera de la curva y
  `build_pumpfun_token_info` las descarta solas.

Conclusión medida: **las cuatro fuentes de "alguien pagó" ya están cubiertas**
(perfil pagado y boosts de DexScreener, Nitro de DEXTools, promo de MoonTok).
Birdeye no añade una quinta. El código está probado y se enciende con una
palabra si algún día se quiere medir la atención pura.

## 4. Telegram: aviso en el momento del TP/SL

Antes salían **dos** mensajes por posición: entrada e informe final. Un TP que
saltaba al minuto 2 no se veía hasta media hora después.

Ahora cada peldaño manda el suyo (`build_ladder_event_notification`) con: qué
saltó, el motivo de compra, precio y movimiento desde la entrada, cuánto vende
ese peldaño, adónde sube el SL protegido, y el **P&L en curso**.

El P&L vive en `tiered_exit.ladder_progress`, junto al resto de la matemática
de la escalera y sin dependencias. Cuenta los peldaños cobrados a su precio de
venta **más** lo que vale el resto al precio actual. Importa: tras TP1 (70 % a
+25 %) un desplome del 50 % sigue leyéndose como **+2,5 %**, no como −50 %. Hay
un test que fija justo ese caso.

También se arrastraba un dato tirado a la basura: el feed de DexScreener trae
`baseToken.name` y se descartaba, así que `TokenInfo` caía al símbolo y los
avisos decían `$BARRON` en vez de `Barron Trump ($BARRON)`. Ya viaja hasta el
registro y todos los mensajes.

> Son hasta 5 mensajes por posición y hay 3 simultáneas. Si resulta ruidoso,
> el filtro `avisos_telegram` del YAML ya existe y deja pasar solo los tipos
> elegidos.

**Ojo con las dos rutas.** `_avisa_peldano` vive dentro de `_paper_watch`, que
solo corre bajo `if self.paper_trade:` (`universal_trader.py:3416`). En dinero
real esa función **no se ejecuta**, así que los avisos de peldaño no salen por
ahí. La ruta real es `_log_trade` → `alert_trade`, y ahora lleva los mismos
datos: nombre y ticker, motivo de compra, movimiento desde la entrada y P&L de
esa venta. El precio de entrada se apunta en `self._precio_entrada` al comprar,
porque `_vender` solo conoce el de salida. Sin ese apunte el P&L se omite en
vez de inventarse.

## 5. El aviso del estudio llegaba vacío y en bucle

`automatizar_estudio.sh` mandaba la cabecera y nada más. Tres fallos:

1. El resumen filtra por `ganadoras|perdedoras|RESULTADO|hacen falta`, y
   `"Todavia no hay operaciones cerradas con P&L real."` no contiene ninguna →
   cuerpo vacío. Ahora hay respaldo: manda las primeras líneas del estudio.
2. `estudiar_operaciones.py` escribe su contador en la **última** línea, pero
   hace `return` antes cuando no encuentra operaciones → el contador nunca
   subía y **el mismo aviso vacío se repetía cada 15 minutos, para siempre**.
   El contador lo lleva ahora el shell (`estudio_contador.json`), escrito
   después de enviar pase lo que pase.
3. El disparador contaba solo líneas `P&L REAL` de los logs — formato del
   sniper viejo. El bot que corre es el de papel y escribe en
   `paper_trades/results.jsonl`. Se cuentan y se resumen **las dos fuentes**.

El texto viaja por variable de entorno, no incrustado en el fuente de Python:
una comilla en el resumen rompía el heredoc.

## 6. Dos utilidades nuevas

- `learning-examples/comprobar_wallet.py` — el bot configura una **clave**, no
  una dirección, así que una `SOLANA_PRIVATE_KEY` equivocada opera desde otra
  wallet sin dar error. Esto deriva la dirección y con `--esperada` dice si es
  la que se creía. **Nunca imprime la clave.**
- `learning-examples/simular_avisos_telegram.py` — recorre dos posiciones
  completas con los mismos builders del bot. Lee la escalera del YAML, así que
  no se desfasa. Imprime por defecto; envía con `--enviar`.

---

## Estado y frentes abiertos

**TP/SL sin tocar.** Siguen los de Codex: SL −18 %, TP1 +25 %/70 %,
TP2 +50 %/10 %, TP3 +75 %/10 %, TP4 +200 %/10 %. Son la hipótesis de partida,
no la conclusión: se calibran con `summarize_signal_results.py` cuando haya
muestra (mediana del pico → TP, mediana de la caída → SL).

**Muestra: cero.** Nada de esto se ha ejecutado nunca contra las APIs reales —
la sesión de desarrollo tenía la red bloqueada. Lógica escrita y testeada, sí;
verificada contra DexScreener, Telegram o Solana, no. El primer arranque en el
droplet es la prueba de verdad.

**Callout de pump.fun: sin hacer.** Investigado, y el dato cambia el diseño:
**un callout cada 6 horas** por cuenta (4 al día), se cobra en USDC a diario
según el volumen que generen tus seguidores, y en agosto cambiaron la fórmula
a **calidad sobre cantidad**. El código actual encola un callout por cada
compra; lo que hace falta no es un publicador que vacíe la cola, sino uno que
**elija el mejor de cada ventana de 6 h**. No hay API pública de escritura
(los callouts son función de la app móvil); existe una especificación no
oficial por ingeniería inversa con `Authorization: Bearer <JWT>`. Antes de
automatizar contra ella conviene leer los términos de Callout Rewards.

**Wallet vieja comprometida.** `94ZSpi…` — su clave estuvo en el historial de
git. El `.env` ya se sacó del repo (`ea588a0`), pero eso no basta: hay que
mover los fondos. Y el token de BotFather, el API hash de Telegram y el del
RPC estaban en ese mismo fichero.

## Verificación

```bash
uv sync
PYTHONPATH=src pytest -q        # 82 pasan
ruff format --check . && ruff check .
```

`pytest` no está declarado como dependencia, así que `uv run pytest` se lo baja
en un entorno aparte **sin** las librerías del proyecto y todo falla al
importar. Hay que lanzarlo dentro del entorno bueno.

Sin regresiones de lint ni de formato. `universal_trader.py` y `bot_runner.py`
**ya venían sin formatear** en `main`: pasarles `ruff format` mete ~450 líneas
de ruido que entierran el cambio real. No hacerlo.
