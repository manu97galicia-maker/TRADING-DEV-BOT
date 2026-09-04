# Datos medidos y hallazgos — sesión 2026-09-02

Documento para que otro agente tenga el contexto completo sin releer el
historial. Todo lo de aquí está **medido en el log de producción**, no
razonado. Donde hay opinión, se dice.

Repo: `manu97galicia-maker/Definitivo_bot` · droplet `159.89.19.12`,
servicio `pump-bot.service`, código en `/root/ai-bot`.

---

## 1. Qué corre ahora mismo

Tres bots en el mismo proceso, repartidos por franja de market cap de entrada
para que **una moneda caiga en uno solo** (sin `min_entry_mc` se solapaban y
doblaban exposición en el mismo token).

| Bot | Entrada | Importe | Dev: migr / tasa | Salida |
|---|---|---|---|---|
| `bot-sniper-devhold.yaml` | MC 0–4.700 | 0,15 | 2 / 10% | 80% fuera en MC 20K, cola 2% a 800K |
| `bot-sniper-devhold-11k.yaml` | MC 4.700–11.000 | 0,07 | 2 / 10% | 80% fuera en MC 47K, cola 2% a 1,9M |
| `bot-sniper-devhold-mayhem.yaml` | MC 0–4.700, solo mayhem | 0,02 | 2 / 25% | todo fuera antes de MC 17K |

Filtros comunes: dev con ≥2 migraciones, dev con ≥10% del supply,
`no_repeat_name_min: 2`, `max_real_buys` 2+2+1 (tanda de prueba acotada).

Protecciones: `suelos_por_mc` (suelos que suben y no bajan; al romperse se
vende todo), trailing (0,75 normal / 0,40 mayhem), stop −50%, y `mc_stop_loss`
900 en mayhem.

---

## 2. EL PROBLEMA ABIERTO: el filtro de supply del dev mide 0,0% SIEMPRE

**41 mediciones, las 41 dieron exactamente 0.0%.** Cero devs con supply.

    DEV OK sharkcat: 11 migraciones de 70 lanzamientos (16%)
    sharkcat: el DEV solo tiene 0.0% (< 10%) -> sin piel en el juego, se omite

Con `min_dev_hold_pct: 0.10` eso bloquea el **100%** de las candidatas, y es la
razón de que la tanda de 5 compras lleve 0.

Hipótesis (sin verificar): la comprobación corre con `compra_inmediata: true`,
a milisegundos de detectar la moneda, y **la compra del propio dev todavía no
ha aterrizado** cuando consultamos su balance. Estaríamos midiendo una cuenta
que aún está vacía.

Hay una tensión de diseño detrás: `compra_inmediata` existe para NO esperar, y
`min_dev_hold_pct` necesita esperar. Las dos juntas se estorban.

**Pregunta para revisión:** ¿es un artefacto de temporización o los devs de
pump.fun realmente no retienen supply al crear? Si es lo primero, ¿esperar N
segundos antes de medir, o leer el `dev_buy` del evento de creación en vez del
balance on-chain? Ver `_dev_holds_too_much` en `src/trading/universal_trader.py`.

---

## 3. Embudo medido (2026-09-02, log parcial por rotación)

| Etapa | Monedas |
|---|---|
| Únicas vistas | 2.728 |
| Descartadas por nombre repetido | 1.530 |
| Dev sin historial | 4.309 (cuenta por bot, hay 3) |
| **Dev con historial (DEV OK)** | **116** |
| Pasaron el 10% de supply | **0** |
| No llegaron al 10% | 41 (todas con 0,0%) |
| Tumbadas por market cap | 3 |
| Sin saldo | 0 |
| **Compradas** | **0** |

Medición anterior del mismo día, con el filtro de nombres aún en 5 min:
4.708 vistas, 2.543 por nombre, 2.140 dev sin historial, 137 DEV OK,
64 sin saldo, 8 compradas.

---

## 4. Distribución de devs (1.921 evaluaciones)

Exigiendo **2+ migraciones**, cuántos perfiles superan cada tasa:

| `min_rate` | Perfiles |
|---|---|
| ≥5% | 58 |
| ≥10% | 38 |
| ≥15% | 23 |
| ≥20% | 17 |
| ≥25% | 10 |
| ≥30% | 7 |

Ejemplos reales del extremo alto: 23 migradas de 32 lanzadas (72%),
23 de 70 (33%), 19 de 70 (27%). No son novatos con muestra de una moneda,
aunque también los hay (4 de 4 = 100%, 2 de 4 = 50%) — **no existe filtro de
mínimo de lanzamientos**, y sería útil.

---

## 5. Resultados reales de la estrategia anterior (REBOTE)

REBOTE compraba el retroceso: moneda que hace pico 3,3–10K, cae a 800–1.400 y
allí recibe N compras consecutivas. Está deshabilitada.

**13 compras, 0 take profits.** Las 11 cerradas salieron todas por mecanismos
de protección:

| Salida | Veces |
|---|---|
| venta muerta | 4 |
| stop loss | 3 |
| trailing stop | 4 |

- 4 de 11 llegaron a ponerse en verde (el trailing exige pico > entrada).
- FERSPE hizo pico en ~2,96x con el TP en x3: se quedó corta, no falló nada.
- 173 retrocesos detectados, **113 rechazados por falta de saldo**.

---

## 6. Hallazgos de código

**`trailing_stop_percentage: 0` NO desactiva el trailing.** *(arreglado)*
`universal_trader.py:1059` hace `trailing_stop_percentage or stop_loss_percentage`,
así que un 0 hereda el stop loss en silencio. Los bots corrían con un trailing
del 50-75% que nadie configuró. Ahora va explícito en los YAML.

**El filtro de supply tenía tres salidas silenciosas.** *(arreglado)* Devolvía
"descarta" sin log cuando faltaba el creador, el supply venía a 0, o fallaba el
RPC. Por eso en 137 monedas no había ni un log suyo. Ahora las tres logean.

**`token_total_supply` no siempre viene en el evento.** *(arreglado)* Se
descartaban monedas por no poder medirlas. Ahora usa la constante
`_PUMP_TOTAL_SUPPLY` (pump.fun es siempre 1e9).

**`UnsupportedQuoteMint` (error 6063).** *(sin arreglar)* pump.fun saca tokens
cuya curva no cotiza en SOL; el bot los aprueba y falla al comprar. No cuesta
dinero (falla en simulación) pero pierde el candidato. El `CreateEvent` no trae
el quote mint, así que filtrarlos exige leer el estado de la curva antes.

**Una `RPCException` sin capturar tumbó el servicio** una vez (01:59).
`Restart=always` lo relevantó, pero mientras estuvo caído no vigilaba posiciones.

**Helius da 500s intermitentes.** Tiró el WebSocket ~30 min y sigue fallando
consultas sueltas; cada fallo descarta un candidato.

---

## 7. Posiciones huérfanas pendientes de venta manual

    LMAO   HoVbhk3xKaQUHxgW5FM8oLAiAxWkYPgUvaGn3pB7pump
    ZPILL  B3hT3UukNWZBUzFZyvWZtH6uwRHmaL6QyARURxa4pump

---

## 8. Frente abierto: BSC / four.meme

Se quiere replicar la idea en BSC: coger las 20 mayores wallets de
`0x2878b01c6aa7099ed88ddb4d2951f1c887037777` y snipear por correlación con
0,025 BNB.

Verificado en cadena: el token es **Wojak (WOJAK)**, un **proxy mínimo EIP-1167**
que apunta a la implementación `0x024f18294970b5c76c0691b87f138a0317156422` —
el patrón de four.meme.

**Bloqueado por acceso a datos.** Ningún endpoint gratuito sirve:

| Vía | Resultado |
|---|---|
| BscScan API V1 | deprecada |
| Etherscan API V2 | free tier no cubre BSC |
| `eth_getLogs` en 5 RPC públicos | `limit exceeded` / requiere token |

Hace falta un RPC de BSC con clave y soporte de archivo (NodeReal, Ankr,
PublicNode). **El repo no tiene NADA de EVM**: cero `web3`, cero firma
Ethereum. `puente_sol_a_bnb.py` solo mueve fondos vía deBridge desde el lado
Solana. Operar en four.meme es construir una plataforma nueva, no configurar
un bot.

---

## 9. Lo que NO está validado

Todo lo de la sección 1 son **razonamientos, no datos**: los escalones, los
suelos, los umbrales de tasa y los tamaños se eligieron discutiendo, no
midiendo. DEVHOLD lleva **0 operaciones**.

Existe `paper_trade` en los filtros (ver `universal_trader.py` ~1543, ya calcula
pico y valle y simula la escalera). Nadie lo ha usado. Sería la forma de tener
cientos de operaciones simuladas sin gastar SOL antes de seguir afinando
parámetros a ojo.
