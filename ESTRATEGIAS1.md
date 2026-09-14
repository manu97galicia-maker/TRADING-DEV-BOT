# ESTRATEGIAS 1 — estado del bot y por qué está así

Documento de traspaso. Todos los números salen leídos de
`bots/bot-paper-ai-entry.yaml` y del código el **14-sep-2026**, no de memoria.
Si algo aquí no cuadra con el YAML, **manda el YAML**.

> **DINERO REAL.** El fichero se llama `bot-paper-ai-entry.yaml` y su cabecera
> dice «PAPER ONLY», pero `paper_trade: false`. El bot compra de verdad. Lo
> desfasado es el nombre, no el ajuste.

---

## 1. Qué hace, en una frase

Busca monedas de pump.fun por cuatro vías, las filtra, compra entre 0,23 y
0,49 SOL, y sale por una escalera de diez take-profit con un stop del −18%
que sube al precio de compra en cuanto cobra el primer peldaño.

---

## 2. La escalera de venta

Los porcentajes son de la cantidad **original**, no de lo que queda. Por eso
suman 98 limpio.

| | Sube un | O sea | Vende | Acumulado | Stop pasa a |
|---|---|---|---|---|---|
| TP1 | +30% | x1,3 | 30% | 30% | precio de compra |
| TP2 | +50% | x1,5 | 10% | 40% | +30% |
| TP3 | +70% | x1,7 | **35%** | 75% | +50% |
| TP4 | +230% | x3,3 | 6% | 81% | +70% |
| TP5 | +400% | x5 | 6% | 87% | x3,3 |
| TP6 | +600% | x7 | 6% | 93% | x5 |
| TP7 | +900% | x10 | 1,5% | 94,5% | x7 |
| TP8 | +1.900% | x20 | 1,5% | 96% | x10 |
| TP9 | +4.900% | x50 | 1% | 97% | x20 |
| TP10 | +9.900% | x100 | 1% | 98% | x50 |
| moonbag | — | — | **2%** | 100% | lo vende Manu a mano |

**Del TP4 en adelante son múltiplos, no porcentajes sueltos.** `tp4_gain: 2.30`
significa x3,3, no +2,3%. Es el error que más caro saldría: leído mal, el
peldaño de x10 se dispararía a +9% y vendería casi todo enseguida. Hay una
prueba que lo vigila (`test_una_ganancia_de_9_significa_x10_y_no_mas_9_por_ciento`).

**El TP3 es el peldaño que decide.** Concentra el 35% de la posición. Una
moneda que se queda en +60% y se da la vuelta deja cobrado el 40%; una que
cruza el +70% deja el 75%. Toda la rentabilidad depende de cuántas cruzan esa
línea, y **eso todavía no está medido**.

### Medido sobre recorridos de precio reales

Simulaciones con las funciones de verdad (`tests/test_tp_sl_recorridos.py`):

| Escenario | Resultado | Queda |
|---|---|---|
| Rug tocando justo el −18% | −18,0% | 0% |
| +30% y vuelta a la entrada | **+9,3%** | 0% |
| Se queda en +60% y cae | +26,5% | 0% |
| Llega a +70% y se hunde | +51,8% | 0% |
| Escalera entera hasta x100 | +501% | 2% |

---

## 3. Salidas y protección

| Ajuste | Valor | Qué hace |
|---|---|---|
| `stop_loss_percentage` | 0.18 | −18% desde la entrada, o desde el suelo que haya subido el trinquete |
| `stop_a_entrada_tras_tp` | true | Cobrado un peldaño, el suelo sube al **anterior**. Tras el TP1, a la entrada |
| `vender_todo_en_stop` | true | El stop se lleva el 100%, moonbag incluido |
| `venta_muerta_seg` / `_gain` | 180 / 0.02 | A los 3 min mira **una vez**: si no ha hecho +2%, vende entera. Si va por encima, no vuelve a preguntar nunca |
| `venta_bajo_entrada_seg` | 180 | 3 min **seguidos** bajo la compra. El contador se reinicia si recupera |
| `anti_rug_ratio` / `_seg` | 2.0 / 4 | Las ventas doblan a las compras en 4 s → fuera |
| `max_hold_time` | **0** | Sin reloj. Quitado a propósito para que los x10–x100 tengan tiempo |
| `abandonar_tras_seg` | 86400 | Tope duro de 24 h vigilando una posición invendible |
| `trailing_stop_percentage` | 0 | Desactivado |

**Los take profit no caducan.** Lo que puede sacar una posición antes de un TP
son las cuatro reglas de arriba, no el reloj.

### El trinquete es lo que sostiene la estrategia

El suelo sube al escalón **anterior**, no al recién tocado. Pegarlo al precio
actual haría que cualquier respiro normal cerrara la posición. Hay una prueba
que comprueba que el suelo nunca queda por encima del precio que lo arma
(`test_el_suelo_nunca_queda_por_encima_del_precio_que_lo_arma`).

---

## 4. Dinero y posiciones

| Ajuste | Valor |
|---|---|
| `buy_amount` | 0.3713 |
| `buy_amount_tras_sl` | 0.3713 |
| `tope_compra_sol` | 0.4950 (recorta cualquier importe por encima) |
| `max_posiciones` | 5 |
| `min_reserve_sol` | 0.03 |
| `valor_minimo_posicion_usd` | 1.0 |
| `buy_slippage` / `sell_slippage` | 10% / 10% |

**Dos frenos, gana el que se quede corto: el techo de 5 y el saldo.** Antes de
cada compra:

```
saldo − min_reserve_sol − lo comprometido ≥ compra + 0,003
```

Poner 5 no hace que haya 5. Con la compra tope:

| Saldo | Posiciones |
|---|---|
| 0,5 SOL | 0 |
| 1,0 SOL | 1 |
| 2,0 SOL | 3 |
| 2,6 SOL | 5 |

**Por qué 5 y no más:** cada posición consulta su precio **cada segundo** y el
nodo da 15 por segundo. Pasadas ~7, las consultas se encolan y el stop
reacciona tarde. El aviso «NODO AL LÍMITE» salta a partir de 8.

**Posiciones de menos de 1 $:** sueltan su plaza y dejan de vigilarse. No es
limpieza, es economía: vender cuesta comisión de prioridad más renta de cuenta,
así que por debajo de un dólar la venta se lleva más de lo que recupera. **No
se vende**, se queda en la wallet.

---

## 5. Las cuatro fuentes de compra

### 5.1 Curva casi llena (`curva_96`) — la única con ventaja real de velocidad

Compra cuando a una curva de pump.fun le queda un 5% para llenarse.

```
umbral 0.95 · tope 0.995 · buy_amount 0.26
max_compras 3 · parar_tras_perdidas 2
```

**De dónde sale el dato, y por qué importa.** Cada `TradeEvent` de pump.fun
publica las reservas de la curva después de ejecutarse. El bot ya lee ese
stream entero (`src/monitoring/volumen.py`), así que el avance de **todas** las
curvas vivas sale **gratis y en el mismo segundo**. Preguntarle al nodo el
estado de cada curva serían miles de llamadas por vuelta contra un presupuesto
de 15/s: no cabe.

Abre su **propia** suscripción a los logs porque cuando hay fuentes de
adquisición activas el trader no arranca el listener de creaciones.

**Anti-bundle.** Un bundle se monta en el bloque de creación: varias carteras
del mismo dueño comprando a la vez que nace la moneda.

| Filtro | Vio nacer | No vio nacer |
|---|---|---|
| Carteras en el bloque cero | máx 3 | — |
| SOL cargado al nacer | máx 2 | — |
| Compradores distintos | mín 30 | mín 30 |
| Concentración de titulares (en cadena) | ≤ 50% | **≤ 35%** |

`exigir_ver_creacion: false` — compra también las que ya estaban en marcha.
**Trampa documentada:** en una moneda que no se vio nacer, «carteras del primer
bloque» NO vale cero: vale las del primer bloque que el bot casualmente miró,
que puede ser cualquiera de su vida. Ese número no dice nada sobre bundles y
dispararía el filtro igual que si lo dijera. Por eso esos dos avisos **se
saltan explícitamente** a ciegas, en vez de dejarles opinar.

### 5.2 DexScreener pagado (`buy_on_dex_paid`)

```
poll_interval 3 · candidatos_por_vuelta 30
incluir_migradas true · incluir_cualquier_origen true
```

Importes por tramo de boosts (en **código**, `src/monitoring/dexpaid_source.py`):

| Boosts | pump.fun | Raydium/otros |
|---|---|---|
| ≤ 50 | 0,325 | 0,234 |
| > 50 | 0,390 | 0,260 |
| Pagó perfil **y** boosts | ×1,2 | ×1,2 |

Régimen estricto para las que **no** nacieron en pump.fun: holder máx 5%,
market cap mín 6.000 $, liquidez bloqueada ≥50%, cupo de 3 compras y corte a
las 2 pérdidas.

**⚠️ HAY UN AJUSTE MUERTO.** `boost_extra_factor: 1.3333` está puesto pero
`boost_extra_umbral: 0`, y el código trata el cero como apagado
(`factor_por_boosts` devuelve 1.0 si `umbral <= 0`). **Ese multiplicador nunca
se aplica.** Se pidió «un tercio más a las que paguen 30 boosts o más». Para
arreglarlo: `boost_extra_umbral: 30`.

**Latencia, y es el techo de esta fuente.** El pago a DexScreener es una
transacción en la cadena, visible al instante, pero su **API** lo publica
minutos después. Nuestra parte va en 3-8 s (medido; sale en el latido de
Telegram como «Vuelta real»). Los minutos son de ellos y no se pueden recortar
por esta vía.

### 5.3 DEXTools DEXTboard (`dextools_hotpools`) — **NO ARRANCA**

```
poll_interval 15 · top 10 → 0.4950 · top 20 → 0.4208 · resto → 0.3713
```

Compra una moneda **la primera vez** que aparece en el ranking
(`DexBoardEntrantTracker`), o sea justo cuando entra en tendencia.

**Falta `DEXTOOLS_API_KEY` en el `.env` del droplet.** El log dice
`Fuente DEXTools: falta DEXTOOLS_API_KEY en el .env; no arranca`. También
admite `DEXTOOLS_API_TIER` (`trial`/`standard`/`advanced`/`pro`).

### 5.4 Canales de Telegram (`telegram_calls`)

```
canales: dextools_nitro, cryptolistings, Wagbocooks
sufijos: pump · promotion_threshold 500
normal 0.3713 · nitro 0.4208 · promocionado 0.4950
```

`acquisition_source` guarda **el nombre del canal**, no un genérico, así que el
resumen por fuente separa los callers solo. Necesita `TELEGRAM_API_ID` y
`TELEGRAM_API_HASH` **y además autorizar la sesión** con
`learning-examples/autorizar_telegram.py`. **Sin verificar si llegan mensajes.**

### Apagadas
`pumpfun_dev`, `birdeye_trending`, `discord_calls`, `gmgn_signals`.

> `gmgn_signals` necesita `gmgn-cli` (binario externo con clave) y además lee
> compras de smart money, **no** el ranking de tendencias.

---

## 6. Filtros de calidad

| Filtro | Valor |
|---|---|
| `max_holder_pct` | 15% (por moneda: las de fuera traen 5% en `additional_data`) |
| `max_token_age` | 120 s |
| `rugs_para_bloquear` | 1 |
| `price_check_interval` | 1 s |
| `cleanup.mode` | `after_sell` — recupera ~0,00204 SOL por cuenta cerrada |
| `cleanup.force_close_with_burn` | false — no se queman tokens por la renta |

---

## 7. Cosas que pueden sustituir la estrategia sin avisar

**Se apagan por AUSENCIA de una clave en el YAML, no por estar a cero.** Hoy
los cinco están apagados y todas las monedas usan la escalera y el stop de
arriba. Dos pruebas lo fijan (`tests/test_tp_sl_recorridos.py`).

| Clave | Qué pasaría si se pone |
|---|---|
| **`bro_address`** | Apuntando a una watchlist `.json`, los devs con pico ≥120.000 $ pasan a **tres** peldaños (+100/+300/+900) con **moonbag del 40%** |
| `tp_marcados` / `tp_sell_marcados` | Escalera aparte para devs marcados |
| `dev_elite` / `dev_mediana` | Stop loss distinto del −18% |
| `mc_stop_loss` | Stop por market cap absoluto |

---

## 8. Historial de esta sesión (13–14 sep 2026)

Commits en orden, sobre `claude/private-key-env-access-cc1er2`:

| Commit | Qué |
|---|---|
| `0a23db7` | Fuente nueva: compra al 96% de curva, sin bundles |
| `26ad16b` | El cupo cuenta posiciones **abiertas**, no intentos |
| `0009b08` | Escalera nueva: 30/10/35 a +30/+50/+70 |
| `cd18f58` | Techo de 5 posiciones |
| `39d0f54` | Todos los importes de compra +30% |
| `7d8a0b0` | Verificación de TP/SL sobre recorridos completos |
| `2945fb5` | No preguntar a la curva de una migrada |
| `93a8fb7` | Comprar también las curvas que no vio nacer |
| `470286c` | Avisar **una** vez de la migración, no una por segundo |
| `82be7c4` | No gestionar posiciones de menos de 1 $ |
| `7dd9808` | El stop cierra el 100% + recuperar renta |
| `99549cd` | Disparo de curva del 96% al 95% |
| `0f0b549` | `deploy/resumen_dia.py` |
| `b8e16aa` | `deploy/revisar_wallet.py` |
| `040a441` | Apuntar origen y motivo en `trades.log` |
| `990801a` | `deploy/espiar_pagos_dex.py` (observador) |
| `4d79769` | **Retomar posiciones al reiniciar** |

### Los tres fallos que costaban dinero

**1. Las posiciones se abandonaban en cada reinicio** (`4d79769`). Era la causa
de «el take profit y el stop loss no saltan». Al arrancar, el bot leía el
marcador de cada posición abierta, avisaba de «bolsa huérfana», **borraba el
marcador y no volvía a mirarla**: la moneda se quedaba en la wallet **sin stop
loss**. Con diez reinicios en dos días, prácticamente ninguna posición llegó a
gestionarse. Ahora se retoman; la cantidad se lee de la **cadena**, no del
fichero, para no vender a ciegas una cantidad que no se sabe.

**2. El cupo de prueba se gastaba en intentos fallidos** (`26ad16b`). «Prueba 3,
si en dos perdemos paramos» contaba al **intentar** comprar. Al 95% de curva la
moneda puede migrar y la transacción falla: tres fallos se comían la prueba sin
abrir una sola posición. Y «si en dos perdemos» no se sostiene sobre compras
que nunca entraron.

**3. Un traceback por segundo y por posición migrada** (`2945fb5`, `470286c`).
El monitor pedía el precio a la curva de una moneda ya migrada, fallaba, y
sacaba diez líneas de traceback — cada segundo. En un droplet de 1 GB eso llena
el disco, y **con el disco lleno el bot no puede ni vender**. Además tiraba una
llamada al nodo por segundo del presupuesto que necesita el stop loss.

### Herramientas nuevas

```bash
uv run deploy/resumen_dia.py [dias]    # operaciones + ranking por fuente
uv run deploy/revisar_wallet.py        # saldo, posiciones reales, renta atrapada
uv run deploy/espiar_pagos_dex.py      # observa la caja de DexScreener (no compra)
```

---

## 9. Lo que está sin verificar

Nada de esto está comprobado contra la realidad. **No dar por hecho que
funciona.**

- **El camino de compra de mints EXISTENTES** (no recién creados) nunca se ha
  verificado contra una compra real. Hay avisos en `token_builder.py` y en las
  tres fuentes.
- **Telegram**: nunca se ha confirmado que llegue un solo mensaje. El log dice
  «connecting», que no es «conectado». Comprobar
  `wc -l trades/telegram_calls.log`.
- **DEXTools**: no arranca, falta la clave.
- **Cuántas monedas cruzan el +70%**: es lo que decide si la escalera está bien
  repartida. Sin eso, cualquier ajuste del TP3 es adivinar.
- **ROI por caller**: se empezó a grabar el 14-sep. Las anteriores salen como
  `listener`.

## 10. Deuda conocida

- El `.env` estuvo en git y sigue en el histórico, en el commit `229d705`.
  Manu decidió no limpiarlo.
- El nombre del fichero de config dice PAPER y opera en real.
- `src/trading/universal_trader.py` tiene ~249 hallazgos de ruff de base.
  **No pasarle `ruff format`**: genera ~450 líneas de ruido que entierran
  cualquier cambio funcional. Editar a mano y comprobar que el contador no sube.

## 11. Cómo se trabaja aquí

```bash
uv run pytest tests/ -q     # 309 pruebas, todas en verde
ruff check <fichero>        # comparar contra la base, no exigir cero
```

Las pruebas llevan los números **a pelo a propósito**: quien toque un ajuste
que mueve dinero ve el test en rojo y tiene que mirar qué gana o pierde antes
de seguir. Si un test se pone rojo al cambiar un número, **eso es el test
haciendo su trabajo** — hay que actualizarlo entendiendo el cambio, no
silenciarlo.

En el droplet (`/root/ai-bot`, servicio `pump-bot`):

```bash
cd /root/ai-bot && git pull && systemctl restart pump-bot
systemctl is-active pump-bot
journalctl -u pump-bot -n 60 --no-pager | grep ENABLED
```
