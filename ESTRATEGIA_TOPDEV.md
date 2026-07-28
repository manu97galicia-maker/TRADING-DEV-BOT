# Estrategia Snipe por Creador (Top Devs) — Documento maestro

_Última actualización: 2026-07-25_

Documento único que consolida todo lo hecho sobre la estrategia de snipear las
monedas que lanzan dev wallets seleccionadas de pump.fun.

---

## 1. Qué hace esta estrategia

Vigilas una lista de **dev wallets** (creadores) de pump.fun. Cuando **una de
ellas crea una moneda nueva**, el bot la compra automáticamente con tus
parámetros y la vende por TP/SL. Es "copy trading" pero del **creador**, no de un
comprador → entras en el segundo 0, al precio más bajo posible.

**Flujo:** escuchar creaciones → ¿la creó una de mis wallets? → ¿tiene liquidez
real en la curva? → comprar → vender por tramos (TP) o cortar (SL).

---

## 2. Cómo se construyó (cambios de código)

`bro_address` ahora acepta **una wallet, una lista, o un archivo `.json`/`.txt`**
(antes solo una). Archivos tocados:
- `src/trading/universal_trader.py` — `_normalize_creators()` + `_load_creator_file()`.
- 4 listeners (`universal_logs/block/geyser/pumpportal_listener.py`) — filtran
  contra el conjunto de wallets.
- **Seguridad:** `None` = sin filtro; conjunto VACÍO = **no compra nada** (evita
  que un watch sin rellenar snipee TODO). Verificado con tests.

Archivos de datos/config:
- `bots/bot-sniper-topdev.yaml` — la config del bot.
- `top_devs.json` — la lista de wallets vigiladas (el bot lee el campo `wallet`).
- `top_devs_ranking.md` — ranking completo de las 27 wallets analizadas.

---

## 3. La config (`bots/bot-sniper-topdev.yaml`)

| Parámetro | Valor | Qué significa |
|---|---|---|
| `platform` | `pump_fun` | Solo tokens de pump.fun (nada de otros DEX) |
| `buy_amount` | `0.05` SOL | Cuánto mete por compra |
| `buy/sell_slippage` | `0.35` | 35% de margen de precio |
| `take_profit_percentage` | `0.5` (+50%) | 1er objetivo... |
| `take_profit_sell_percentage` | `0.6` | ...vende 60% |
| `tp2_gain / tp2_sell` | `1.5` / `0.4` | a +150% vende el 40% restante |
| `stop_loss_percentage` | `0.35` (-35%) | corta pérdidas |
| `max_hold_time` | `1800` (30 min) | cierre por tiempo |
| `listener_type` | `logs` | método de escucha |
| `max_token_age` | `30` s | ventana para comprar tras el nacimiento |
| `bro_address` | `top_devs.json` | **las wallets a vigilar** |
| `yolo_mode` | `true` | continuo: snipea CADA lanzamiento |
| `min_curve_sol` | `2` | **anti-basura**: exige ≥2 SOL en la curva o salta |
| `paper_trade` | `true` | **SIMULACIÓN** (no gasta SOL) |
| `enabled` | `false` | fuera del runner real (se corre aparte) |

### Filtros anti-basura (tu preocupación de "sin liquidez")
- **Plataforma**: solo pump.fun. ✅
- **Liquidez**: en pump.fun todo token nuevo tiene liquidez por la bonding curve
  (siempre puedes vender). El riesgo real son lanzamientos muertos → `min_curve_sol: 2`
  exige que haya dinero real en la curva antes de comprar.
- **Reputación del dev**: la lista solo tiene devs con buena mediana/migración.
- **Stop-loss**: corta rápido los que fallan.

---

## 4. Las wallets vigiladas (23 activas)

Fuente: GMGN top_dev, snapshot 2026-07-25. `med` = mediana ATH market cap
(métrica fiable). Corte de calidad aplicado: **mediana ≥ ~$33K**.

| # | Wallet | Lanz./día | % Mig | Mediana MC |
|---|---|---|---|---|
| 1 | yHCxHBEaJW5tbndqC8JciSThr7U1cqLpdcsvHcx6PRe | 8.19 | 28.4% | $386.8K |
| 2 | 5dUXg97Ltrjid2KVTQAb6p6PVZXBneHcgV1Z8A9xzKgg | 3.61 | 58.5% | $49.2K |
| 3 | GeBJSHK4WsGrz2HRvTbqvWGx4JRMpHfJG2ikzrYBDuwR | 2.10 | 31% | $168.3K |
| 4 | HyYNVYmnFmi87NsQqWzLJhUTPBKQUfgfhdbBa554nMFF | 0.96 | 52.4% | $126.3K |
| 5 | ARW9NzhpuBVYaYBZo6fW1P1U6LTNwUY6jfi7XC37Sa97 | 0.49 | 62.9% | $88.2K |
| 6 | 3vU5HSbmB4k29ezFPYeLxLtmzG2Z5Fqph1FtegMcptYd | 0.41 | 85.7% | $61.2K |
| 7 | DqJdZcTCXtMx6H6SZMV2CSBLVdfvdHG9rLciJB1VoLTd | 0.23 | 80% | $127.7K |
| 8 | ANq7FDQrELwS9nMcT5foN9EZn8hR1SFHjjswcPnHuS1s | 0.21 | 100% | $654.5K |
| 9 | G4krkerMkeYw7ffTUgdf7qXEXnuMGHpWUESBQpYqvtwU | 0.20 | 65.4% | $59.85K |
| 10 | 39ZPo8CUxAXQFuc57EymAKVMTYtAXYsCnAKw74BW2Rac | 0.16 | 90.9% | $35.4K |
| 11 | BSY59UH2CvMAcE6hRpg4wgxJ81MryBRkY9GtpJ846wzp | 0.15 | 66.7% | $36.7K |
| 12 | 8jmPNzP5ioMWm4WsRsuxaYHfVc9DqjecnaVwMUSFuMJz | 0.13 | 66.7% | $59.4K |
| 13 | 4x3ePr8EEryyvxRavnkJaqCPF9Pd3BxdsJTQkndYeqCZ | 0.10 | 80% | $45.0K |
| 14 | 5KTX7LZyoewhQ2keNCNQ6qQCXW9uermzr8r5K6uss4PY | 0.10 | 77.8% | $45.9K |
| 15 | 4wcLkscW59MJUAoPAu5zA6aiKMZnbBRbVf5vm8BcGeVs | 0.095 | 66.7% | $39.6K |
| 16 | DPiQs7yP5WnBxN7RwttMKkrndmeTzeVZ69sxeAswxm8r | 0.08 | 41.9% | $63.0K |
| 17 | 4Q5LLWkgkpJ4McQ5C16fj5szFAhXEpqBUmYtpRZnAUei | 0.07 | 100% | $202.8K |
| 18 | 8SGpzbiPyRBJCmDw9BeJuuHG1f6K2Bu8Aij6Wia7n1Xg | 0.064 | 66.7% | $36.3K |
| 19 | 6rCzKkKyYUTEX4cw3yBS9ShLYrvPUoo9Hb67wfkhB1Wc | 0.06 | 56.2% | $33.75K |
| 20 | 9bRXg6zNYB5uW1JEZdbpKBVZLQRyRDXNnB5Ugi1nLQm7 | 0.056 | 100% | $290.2K |
| 21 | Cx3CBLZrBiAZUCz5KCidKKsPhuExmHVWrz67ymE1a3gk | 0.04 | 94.4% | $194.4K |
| 22 | 6h4Mk1hhuQpUxSKZaSEX6RC23C77Bpi3ZvWmdLQkEMyv | 0.038 | 100% | $464.6K |
| 23 | HssxU1tv66q1AVDSexK2rro37tjaag83y61iBTinp7ns | 0.026 | 100% | $71.7K |

**Frecuencia total: ~17.6 lanzamientos/día** (~1 cada 82 min). Muy concentrada:
yHCx (47%) + 5dUX (21%) + GeBJ (12%) = **80% del volumen**.

### 4 apagadas (mediana <$20K = ruido, fuera del watch)
| Wallet | Lanz./día | % Mig | Mediana MC |
|---|---|---|---|
| 4q4GKBpVmXGhXYNaR4DetQjf5WjHEHhbJ9Wgybt7F8Yu | 6.12 | 32.7% | $12.4K |
| HH7jLETzcSBnFQcAG5D4VUiFYPSATTyhu5R3QCgzW2M | 0.30 | 32.3% | $15.65K |
| FePSGRavtQty99PFUty5WnaK6M7eibcEEpZRcrmZ6Xi1 | 0.12 | 40% | $19.4K |
| GKVhR9BuJUTDkV6KC2i6QGujJ1PZjVn9KR66fsi1jwBp | 0.049 | 37.5% | $16.9K |

_(4q4G lanza mucho — 6/día — pero su mediana $12.4K es la peor: tokens que no
suben. Por eso fuera.)_

**yHCx** está DENTRO como **experimento**: mucho volumen (47%) pero solo 28% migra;
el paper test dirá si su alta mediana ($386K) compensa o solo ensucia.

---

## 5. Estado del PAPER TEST (en marcha)

- Corriendo en **simulación** (nunca compra real). Launcher standalone en
  `scratchpad/run_paper_topdev.py` → llama `start_bot('bots/bot-sniper-topdev.yaml')`.
  Salta el candado `.bot.pid`, NO toca los otros bots.
- Verificado en logs: `watching 23 wallet(s)`, suscrito, y descarta todo lo que
  no crean las 23 ("Token creator not in watch list. Skipping...").
- Resultados se guardan en `paper_trades/entries.jsonl` y `results.jsonl`.

### Cómo pasar a REAL (cuando el paper convenza)
1. Recargar la wallet (ahora ~0.013 SOL, insuficiente).
2. En `bot-sniper-topdev.yaml`: `paper_trade: false`.
3. Bajar `buy_amount` si hace falta (a 17.6/día × 0.05 = ~0.88 SOL/día).

---

## 6. Contexto: por qué se pivotó aquí

Las estrategias ANTERIORES (Dex-Paid) perdían:
- Simulación (181 paper trades): **winrate 6%**, 88% al stop-loss, −20.4×buy_amount.
- Real: wallet **0.44 → 0.013 SOL** ≈ **−0.43 SOL perdidos**.

Objetivo del paper test de topdev: **comparar su winrate contra ese 6%**.

---

## 7. PENDIENTE / decisiones abiertas

- [ ] Dejar correr el paper unos días y medir winrate real.
- [ ] **Decidir si apagar los 3 bots reales** (geyser/blocks/mayhem siguen
      `enabled: true` con SOL real — si recargas, vuelven a operar).
- [ ] Evaluar si yHCx (experimento) ayuda o estorba en los resultados.
- [ ] Afinar `min_curve_sol` (ahora 2) según lo que muestre el paper.

---

## 8. Archivos relacionados
- `bots/bot-sniper-topdev.yaml` — config.
- `top_devs.json` — wallets vigiladas (editable: borra un `wallet` para apagarla).
- `top_devs_ranking.md` — ranking completo de las 27 analizadas.
- `scratchpad/run_paper_topdev.py` — launcher del paper test.
