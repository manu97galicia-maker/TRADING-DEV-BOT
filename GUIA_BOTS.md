# GUÍA DE LOS BOTS — setup en real (actualizado 2026-07-26)

Sniper de pump.fun corriendo **6 estrategias a la vez** sobre una sola wallet.
Todo en real. Este documento resume qué hace cada bot, cómo se sale (TP/SL),
la excepción del filtro de holders, cómo arrancar/parar, y los trucos que hay
que recordar.

---

## 1) Cómo arrancar / parar (IMPORTANTE)

> ⚠️ **`uv` NO está en el PATH** en esta máquina. NO uses `uv run`. Usa el python
> del venv directamente.

**Arrancar (los 6 bots, en segundo plano):**
```bash
cd ~/pumpfun-bonkfun-bot
rm -f .bot.pid
nohup .venv/Scripts/python.exe src/bot_runner.py > logs/runner_console.log 2>&1 &
```
El runner lee TODOS los `bots/*.yaml` y arranca los que tengan `enabled: true`.

**Parar (mata runner + hijos; en Windows los hijos pueden quedar huérfanos):**
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*pumpfun-bonkfun-bot*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
Luego borra el lock: `rm -f .bot.pid`.

**Comprobar que están vivos:** mira `logs/<NOMBRE>_*.log` (deben tener líneas con
la hora actual) o `cat .bot.pid`.

> Cada cambio de config (YAML) requiere **reiniciar** el runner (para todo y
> relanza). Las configs solo se leen al arrancar.

---

## 1-bis) Panel de Telegram (PnL en vivo + botones de venta)

Proceso aparte que NO toca el trading. Muestra cada posición abierta con su PnL
refrescándose y botones **Vender 25% / 50% / 100%** (del restante).

**Arrancar el panel** (junto a los bots):
```bash
cd ~/pumpfun-bonkfun-bot
nohup .venv/Scripts/python.exe -u tg_panel.py > logs/tg_panel.log 2>&1 &
```
> Lanza SOLO UNA instancia (si hay dos, duplica mensajes). Para pararlo: mata el
> python con CommandLine `*tg_panel*`.

**Cómo funciona:**
- Cada bot escribe su posición viva en `open_position<suffix>.json` (mint, PnL,
  restante, valor SOL) cada 2s.
- El panel lee esos ficheros, manda/edita un mensaje por posición en Telegram con
  el PnL y los botones.
- Al pulsar un botón, el panel escribe `sell_cmd<suffix>.json = {mint, frac}`; el
  monitor del bot lo detecta en su próxima vuelta (≤2s) y vende esa fracción del
  restante (razón `manual_XXpct`), con fallback a Jupiter. Cada bot tiene 1
  posición a la vez, así que el `suffix` identifica la posición.
- Credenciales en `.env`: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (bot @MANUFERBOT).

Código: `tg_panel.py` (raíz) + en `universal_trader.py`: `_write_live_status`,
`_check_manual_sell` (llamados dentro de `_monitor_tiered_exit`).

---

## 2) Los 6 bots (todos en real)

Salida IDÉNTICA en todos (ver sección 3). Lo que cambia es **qué compran**.

ESTRATEGIA FINAL (2026-07-26): solo devs probados, por niveles de mediana. Buy
**0.15 SOL/op** · gas **0.005 SOL/tx**.

| Bot | SOL/op | Lista | ROI backtest 7d |
|---|---|---|---|
| **⭐ ELITE** | 0.15 | `top_devs_elite.json` (75, mediana ≥$25K) | **+61.5%** medio · **+23% mediano** · 51% win |
| **QUALITY** | 0.15 | `top_devs_quality.json` (37, mediana $15-25K) | +19.9% medio |
| **DEV-MIGRATE** | 0.15 | — (todas, dev migró ≥25%) | señal independiente (migración) |

> **Hallazgo clave (backtest Dune, 2.623 trades/7d):** la **mediana histórica del
> dev** es EL predictor. Devs mediana ≥$25K → **+61.5% ROI** y el trade mediano es
> **+23%** (la mayoría ganan). Devs mediana <$15K → ~0% medio, mediana −10 a −32%
> (dinero muerto). Por eso solo operamos devs de mediana alta.

**Trades/hora esperados:** ~3–5 (a ráfagas). **Exposición máx:** 0.45 SOL.

**Desactivados** (dinero muerto o redundantes): SWING, FREQ3X (mediana <$15K);
GEMAS, MEDIANA ALTA (highmedian), LISTA3 (init4k) — sus devs de mediana alta ya
están cubiertos por ELITE+QUALITY sin solapamiento; MAYHEM, FUCRH, `bot-sniper-1..6`.

### Filtros de entrada (stack anti-basura, en todos los bots)
Un token debe pasar TODO esto para que se compre:
| Filtro | Valor | Bloquea |
|---|---|---|
| `min_holders` | **6** | < 6 poseedores → bundle/dev-spam NO orgánico |
| `max_entry_mc` | **3500** | MC ya > $3.5K → entrada en el techo de un spike (tipo GigaBull) |
| `max_holder_pct` | **0.05** | un holder con > 5% del supply (excepto excepción por mediana del dev) |
| `min_curve_sol` | **0.5** | curva casi vacía → bundle muerto |
| `holder_bypass_ratio` | **3** | excepción: permite holder >5% si mediana_dev ≥ 3× MC actual |

Código de los filtros: `universal_trader.py` → `_top_holder_exceeds` (holders + concentración),
y el bloque `MC filtro BLOCK` (tope de entrada) dentro de `_handle_token`. Parámetros
cableados en `bot_runner.py`.

### Avisos de Telegram con MOTIVO
- **Compra**: `🟢 COMPRA · dev migró 50% (4/8 lanz) · SYM · 0.15 SOL` — el motivo lo
  arma `_build_buy_reason` (migración del dev, o lista + mediana).
- **Venta**: `🔴 VENTA · ✅ TP +50% / 🛑 STOP LOSS / 👆 Manual 50% · SYM · … SOL` — motivo
  pasado desde `_sell_amount` → `_log_trade` → `alert_trade` (`_reason_label` en `notifier.py`).

**Otros filtros de entrada**: `max_token_age 30s`, `wait_after_creation 6s`
(ver la tabla de filtros de arriba para el stack anti-basura completo).

Exposición máxima simultánea (cada bot mantiene **1 posición** a la vez):
6 × 0.15 = **0.9 SOL**. Wallet ~1.98 SOL → colchón amplio.

---

## 3) Sistema de salida TP / SL (igual en los 6)

Escalera vendiendo **la mitad del restante** en cada nivel:

| Nivel | Se dispara a | Vende (del original) | Queda |
|---|---|---|---|
| **TP1** | **+50%** | 40% | 60% |
| **TP2** | **+150%** | 25% | 35% |
| **TP3** | **+300%** | 15% | 20% |
| **Moonbag** | — | corre libre | **20%** |
| **Stop-loss** | **−40%** | TODO lo que quede | 0 |

> **Config data-driven (análisis Dune 2026-07-26):** un ganador típico llega a
> **4.5x** la entrada (p90 ~15x) → moonbag 20% y TP3 +300% para dejarlo correr. Y un
> SL de −30% te saca del **29%** de los ganadores antes de que peguen (dip temprano);
> a −40% solo del 17% → por eso el SL está en −40%. Slippage de compra 0.15 (no
> perseguir spikes), venta 0.30 (garantizar salida).
| **Max-hold** | **120s** | solo si está **en rojo** | baja al moonbag |

- El precio se comprueba cada **2 segundos**.
- **SL**: si el precio cae a `entrada × 0.70` → vende todo. (Probado en real:
  logs con `STOP LOSS ... Sold (stop_loss)`.)
- **Max-hold (120s)**: a los 2 min **solo vende si vas perdiendo** (precio <
  entrada). Un ganador NUNCA se corta por tiempo; el moonbag corre libre. Hay un
  techo de seguridad a 480s (`max_hold×3+120`) para no quedarse pillado eterno.
- **Venta en curva falla a veces** (`Custom 6003`) → **cae automáticamente a
  Jupiter** y vende igual. No te quedas atrapado.

Código: `src/trading/tiered_exit.py` (lógica de escalones) + `src/trading/
universal_trader.py` (`_monitor_tiered_exit`, SL sobre las líneas ~1465, max-hold
~1511).

---

## 4) Excepción del filtro de holders (por MEDIANA del dev)

Regla base: **no comprar si un holder (que no sea la curva) tiene >5% del supply**
(riesgo de pump&dump).

**Excepción** (solo en bots con lista, que conocen la mediana de cada dev):
```
holder >5%  →  bloquear
   EXCEPTO si  mediana_del_dev ≥ 3 × market_cap_actual  →  comprar
```
Idea: si el dev suele llegar mucho más alto que donde estamos comprando, hay
recorrido y el holder gordo importa menos. Ej: MC ahora $6K, mediana dev $20K
(x3.3) → **compra**; mediana $15K → **bloquea**.

- Solo aplica donde hay lista con `median_peak` por wallet (SWING, LISTA3,
  MEDIANA ALTA, FREQ3X, GEMAS). En **DEV-MIGRATE** no hay mediana por dev →
  bloquea a secas (más seguro).
- Config: `max_holder_pct: 0.05` + `holder_bypass_ratio: 3` en cada YAML.
- Código: `universal_trader.py` → `_top_holder_exceeds` + `_load_creator_medians`
  (`self._creator_medians`). MC actual = `price_per_token × 1e9 × 75.58`.

---

## 5) Las listas de devs — cómo se hicieron (Dune)

Fuente: Dune Analytics, tablas `pumpdotfun_solana.pump_evt_createevent` /
`pump_evt_tradeevent`, ventana 7 días. Market cap (USD) =
`virtual_sol_reserves × 1e6 / virtual_token_reserves × 75.58`.
Scripts en el scratchpad de la sesión (usan `dune.py`, query_id 8103039):

- `build_highmedian.py` → `top_devs_highmedian.json` (mediana pico >$15K, ≥5 lanz/7d).
- `build_freq3x.py` → `top_devs_freq3x.json` (muy frecuentes, mediana ≥3× el
  lanzamiento, con volumen; excluido el spam >40 lanz/día).
- `build_gemas2.py` → `top_devs_gemas.json` (17 estrictas, mediana ≥$20K).
- `build_gemas3.py` → `top_devs_gemas_mas.json` (52 ampliada, la que usa GEMAS).

**Métrica anti-dump (clave de las GEMAS):** por token se calcula el **MC típico**
= mediana del MC en TODOS sus trades (no el pico/max). Un pump&dump tiene MC típico
bajo aunque el pico toque $20K; un climber real que sube y sostiene tiene MC típico
alto. Se exige `MC_típico ≥ 40% del pico` para que sea "sube y se queda", no un wick.
`pct_sostiene = MC_típico / pico`.

---

## 6) Wallet y seguridad

- Wallet operativa: `94ZSpiQKTZAwy8C8jvuE4kvjsUuHUmp3XDQA1pAzYcU3`.
- La private key vive **solo** en `.env` (`SOLANA_PRIVATE_KEY`). `.env` está en
  `.gitignore` — **nunca** se sube al repo. Haz copia de seguridad de la key en
  Phantom.
- Reserva mínima que el bot nunca gasta: `min_reserve_sol: 0.03`.
- Guards activos: 1 posición por bot (`_position_semaphore`), reserva de balance
  bajo lock (dos compras no pueden pasar con el mismo saldo), lock de único dueño
  (`.bot.pid`), anti-mayhem, watchdog anti-huérfano.

---

## 7) Trucos / gotchas que hay que recordar

1. **`uv` no está en PATH** → arranca con `.venv/Scripts/python.exe`.
2. **Nunca `<` o `>` en el `name:`** de un YAML → el nombre del log lo lleva y
   Windows revienta con `OSError [Errno 22]` (ese bot no arranca). Usa "+", "bajo",
   "mayor", etc.
3. En Windows los **hijos de multiprocessing** pueden quedar huérfanos → al parar,
   mata por `CommandLine like '*pumpfun-bonkfun-bot*'` (ver sección 1).
4. Cambiar cualquier YAML → **reiniciar** el runner.
5. Priority fee `fixed_amount`: SOL = `fixed_amount / 1e10`.
   `30_000_000 = 0.003 SOL`, `300_000_000 = 0.03 SOL` (el de GEMAS).
6. Los warnings tipo "Blockhash fetch failed" / `Custom 6003` son **normales**
   (se auto-recuperan por Jupiter). No son fallos que paren nada.
