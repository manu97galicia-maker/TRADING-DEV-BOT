# 🤖 Bot Sniper de pump.fun — Cómo funciona

_Guía sencilla para entender qué hace este bot y cómo._

---

## ¿Qué es esto en una frase?

Un programa que **compra monedas nuevas de pump.fun (Solana) en sus primeros segundos de vida y las vende minutos después** buscando ganancia, todo automático las 24 horas.

Piensa en la pesca: en pump.fun nacen miles de monedas al día. La mayoría son basura, pero algunas suben mucho en los primeros minutos. El bot pone la caña en el sitio justo y engancha las buenas según unas reglas.

---

## El ciclo del bot (4 pasos)

```
1. ESCUCHAR  →  2. FILTRAR  →  3. COMPRAR  →  4. VENDER
```

1. **Escuchar** — El bot está enchufado a Solana y se entera **al segundo** de cada moneda nueva que nace en pump.fun (miles al día).

2. **Filtrar** — No compra todo (eso sería tirar el dinero). Solo compra las que pasan sus **reglas** (ver estrategias abajo).

3. **Comprar** — Si una moneda pasa el filtro, compra al instante una cantidad fija de SOL.

4. **Vender** — Vende automáticamente según un plan de salida (take-profit / stop-loss). Sin emociones.

---

## Las 2 estrategias (corren a la vez)

El bot ejecuta **dos estrategias en paralelo**, cada una compra distinto:

### 🎯 Estrategia 1 — SWING (0.2 SOL por compra)
Vigila una **lista fija de ~90 dev-wallets** (creadores) seleccionados por su buen historial. Cuando **uno de esos devs crea una moneda**, la compra.
> Es como seguir a chefs que sabes que cocinan bien: cuando sacan plato nuevo, lo pruebas.

### 👤 Estrategia 2 — DEV-MIGRATE (0.15 SOL por compra)
Vigila **TODAS** las monedas nuevas, pero solo compra si el **creador tiene buena reputación**: que haya **migrado ≥1 token antes Y con una tasa de éxito ≥20%**.
> Aquí no sigues a una lista fija: el bot comprueba en directo si el creador es "bueno" antes de comprar.

Las dos usan la **misma wallet** y el **mismo plan de venta**.

---

## El plan de venta (igual para las dos)

Cuando el bot compra, ya tiene decidido cómo salir:

| Situación | Qué hace |
|---|---|
| Sube **+80%** | Vende el **40%** de la bolsa (asegura ganancia) |
| Sube **+150%** | Vende otro **40%** |
| El **20% "moonbag"** | Se queda corriendo libre por si hace 10× (se vende a mano) |
| Cae **−30%** | Corta y vende todo (stop-loss) |
| Pasan **2 minutos** sin nada | Cierra la posición (menos el moonbag) |

**La idea clave:** ganadores grandes, pérdidas pequeñas (topadas en −30%). Aunque solo aciertes 1 de cada 4, los ganadores pagan de sobra los fallos.

---

## Cosas importantes

- **Solo pump.fun.** No toca otros exchanges ni monedas "mayhem".
- **Compra cada moneda una sola vez** (nunca repite).
- **Una posición a la vez por estrategia** (no se sobre-expone).
- **Avisos por Telegram:** te llega un mensaje en cada compra y cada venta, con enlace a GMGN para ver la moneda.

---

## ¿Dónde se ajustan las cosas?

Todo se configura en archivos `.yaml` dentro de la carpeta **`bots/`**:
- `bot-sniper-swing.yaml` → la estrategia SWING
- `bot-sniper-devmigrate.yaml` → la estrategia DEV-MIGRATE

Ahí se cambia: cuánto compra, los % de take-profit/stop-loss, los filtros, etc.

Las wallets que vigila la estrategia SWING están en `top_devs_200.json`.

---

## ¿Cómo se arranca?

```bash
uv sync                    # instala las librerías (una vez)
python -u src/bot_runner.py   # arranca todos los bots activos
```

Hay un `watchdog.py` que lo reinicia solo si se cae. En un servidor se deja corriendo 24/7.

Necesita un archivo `.env` (que NO está en GitHub, es secreto) con:
- La clave privada de la wallet
- El endpoint RPC (Helius)
- El token del bot de Telegram

---

## 🔒 Seguridad (MUY importante)

- **NUNCA** compartas ni subas la **clave privada** ni la **frase semilla** de la wallet. Quien las tenga puede vaciar el dinero.
- El `.env` con las claves **nunca** se sube a GitHub (está en `.gitignore`).
- Usa siempre una wallet **dedicada solo al bot**, con poco saldo, nunca tu wallet principal.

---

## ⚠️ Aviso de riesgo

Esto opera con **dinero real** en un mercado **muy arriesgado** (memecoins). La mayoría de monedas de pump.fun son basura o estafas. La estrategia busca ventaja estadística, **pero no garantiza ganancias** — puedes perder. Empieza con poco y no metas lo que no puedas permitirte perder.

---

_Bot basado en el proyecto open-source de Chainstack (pump-fun-bot), con estrategias y mejoras propias._
