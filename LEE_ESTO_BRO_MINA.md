# 🚀 LEE ESTO BRO MINA — Cómo funciona el bot de sniping

Guía de cero para entender qué es esto, cómo snipea, qué wallets usa, qué es Helius
y el servidor, y cómo se maneja todo. Sin tecnicismos raros.

> ⚠️ **Aviso honesto antes de nada:** esto es de **alto riesgo**. Snipear pump.fun,
> de media, para retail es dinero difícil (los que ganan de verdad montan bundles o
> van co-locados). **Solo mete lo que puedas perder** y **mide el P&L NETO real**
> antes de subir cantidades. No es dinero gratis.

---

## 1. En 30 segundos: ¿qué es esto?

Un **bot** que:
1. **Escucha** todos los lanzamientos nuevos de monedas en **pump.fun** (24/7).
2. **Compra automáticamente** (snipea) las que lanza un **dev bueno** de nuestra lista,
   en los **primeros segundos** (cuando la moneda vale ~$4-5K de market cap).
3. **Vende solo** con reglas: toma beneficios por tramos, corta pérdidas, y protege
   las ganancias con un *trailing stop*.

La idea: pillar la moneda **abajo del todo** y venderla cuando sube. No compramos
cualquier cosa — solo devs que **históricamente hacen monedas que se disparan**.

---

## 2. ¿Qué es "snipear" y qué es pump.fun?

- **pump.fun** = una web donde cualquiera crea una memecoin en 10 segundos. Cada moneda
  arranca en una "bonding curve" a **~$4.000–$6.000** de market cap.
- **Snipear** = comprar en los **primeros 1-2 segundos** del lanzamiento, antes de que
  suba, para pillar todo el recorrido.
- La clave NO es la velocidad sola — es **a quién** le compras. Seguimos wallets de
  **devs con buen historial** (monedas que pumpean), y compramos solo cuando ELLOS lanzan.

---

## 3. ¿Qué es el modo MAYHEM?

**Mayhem** es un tipo de lanzamiento especial de pump.fun (la moneda lleva una bandera
`is_mayhem_mode`). En la práctica: **más volátil** — pumpea más fuerte, pero también
dumpea más fuerte. Más varianza.

> Nota honesta: las reglas EXACTAS del modo mayhem (por qué pump.fun lo gamifica así)
> mejor confírmalas en la web de pump.fun. Aquí lo tratamos como "lanzamientos más
> salvajes de ciertos devs que se les da bien el mayhem".

Tenemos un bot dedicado que compra **SOLO** monedas mayhem, y **solo** de 15 devs cuyos
mayhem se disparan de verdad (sacados con datos de Dune, ver punto 5).

---

## 4. Las 3 estrategias (3 bots corriendo a la vez)

Cada estrategia es un archivo `.yaml` en la carpeta `bots/`. Comparten la misma wallet.

| Bot | Archivo | A quién compra | Cómo escucha |
|-----|---------|----------------|--------------|
| **ELITE** | `bot-sniper-elite.yaml` | ~27 devs con mediana de pico ≥$25K que pagan DEX/CTO | pumpportal (feed gratis) |
| **NEXT-BIG** | `bot-sniper-nextbig.yaml` | 102 devs "moonshot" / que sostienen el precio, mediana ≥$25K | pumpportal |
| **MAYHEM** | `bot-sniper-mayhem.yaml` | 15 devs cuyos **mayhem** se disparan | Helius WSS (listener `logs`) |

> El bot MAYHEM usa el listener `logs` (Helius) porque el feed gratis (pumpportal)
> **no dice** si una moneda es mayhem — esa bandera solo la lee Helius.

---

## 5. ¿Qué wallets usa? (las listas)

Hay dos tipos de listas:

**a) Devs que SÍ compramos** (los buenos), un archivo por estrategia:
- `top_devs_final.json` → ELITE
- `top_devs_nextbig.json` → NEXT-BIG
- `top_devs_mayhem.json` → MAYHEM

Cada wallet ahí es un **dev** (creador). Cuando ese dev lanza una moneda, el bot la compra.

**b) Devs que NUNCA compramos** (blacklist):
- `blacklist_devs.json` → devs vetados a mano (ruggers, pump&dumpers). El bot los ignora siempre.

**c) La wallet PROPIA** (con la que el bot compra y vende):
- Vive en el archivo `.env` (variable `SOLANA_PRIVATE_KEY`). **Esto NO se comparte NUNCA.**
- Su dirección pública es la wallet de trading; el saldo en SOL es la munición del bot.

> ¿Cómo se sacan los devs buenos? Con **Dune** (una web de analítica de blockchain):
> consultamos qué devs lanzan monedas que pumpean, con qué mediana de market cap, qué %
> se disparan, etc. Las queries están en `dune_mayhem_devs.sql` y `dune_mayhem_devs_v2.sql`.

---

## 6. ¿Cómo snipea exactamente? (el flujo, paso a paso)

Por cada lanzamiento nuevo el bot hace esto en milisegundos:

1. **¿El dev está en mi lista?** Si no → lo ignora.
2. **¿Está en la blacklist o auto-baneado?** Si sí → lo ignora.
3. **(solo MAYHEM) ¿es una moneda mayhem?** Si no → lo ignora.
4. **COMPRA** X SOL al instante (0.15 ELITE/NEXT-BIG, 0.1 MAYHEM).
5. **Vigila el precio** y vende por reglas:
   - **Take-profit por tramos:** vende un cacho a +50%, otro a +150%, otro a +300%.
   - **Stop-loss:** si cae -40% desde tu entrada, corta.
   - **Trailing stop (anti pump&dump):** si sube y luego cae -40% **desde su pico**,
     vende TODO (incluido el "moonbag") para no comerse el desplome.
   - Deja un pequeño "moonbag" corriendo por si se va a la luna.
6. **Auto-baneo automático:** si un dev te hace **perder dinero 2 veces seguidas**,
   el bot lo **bloquea solo** y no le vuelve a comprar.

---

## 7. ¿Qué es Helius?

**Helius** es un proveedor de **RPC** de Solana. Un RPC es la "puerta de entrada" para
hablar con la blockchain: leer datos (precios, saldos) y **mandar transacciones**
(las compras y ventas). Sin un RPC bueno, el bot no puede operar.

Usamos el **plan de PAGO** de Helius (el gratis se agota y da error `429` → el bot deja
de comprar). Dos endpoints:
- **RPC "Secure"** (`https://...`) → para firmar y enviar las compras rápido.
- **WSS** (`wss://...`) → un "websocket" para **escuchar** los lanzamientos en directo
  (lo usa el bot MAYHEM).

> 🔑 La **API key** de Helius vive en el archivo `.env` (dentro del endpoint). **No se
> comparte.** Si un día el bot no compra y ves `429 / max usage reached`, es que la key
> se quedó sin créditos → hay que renovar plan o key.

---

## 8. El servidor (VPS) — dónde vive el bot

- **Proveedor:** DigitalOcean. **SO:** Ubuntu 22.04. **IP:** `159.89.19.12`.
- El bot corre como **servicio de systemd** llamado **`pump-bot`** → arranca solo al
  encender, y se **reinicia solo** si crashea.
- **Carpeta del bot:** `/root/ai-bot`.
- Ventaja de tenerlo en el VPS y no en el PC: corre **24/7** aunque apagues el ordenador.

---

## 9. Chuleta de comandos (en el terminal del VPS)

Primero entras:
```bash
ssh root@159.89.19.12
```

Luego, dentro:
```bash
cd /root/ai-bot

# Ver si está corriendo
sudo systemctl status pump-bot

# Parar / arrancar / reiniciar
sudo systemctl stop pump-bot
sudo systemctl start pump-bot
sudo systemctl restart pump-bot

# Ver los logs (los 3 bots)
ls -t logs/*.log | head -3
tail -f "$(ls -t logs/*.log | head -1)"    # ver en directo (Ctrl+C para salir)
```

**Actualizar el código** (cuando cambiamos algo y lo subimos a GitHub):
```bash
cd /root/ai-bot
curl -sL https://github.com/manu97galicia-maker/Definitivo_bot/archive/refs/heads/main.tar.gz | tar xz --strip-components=1
sudo systemctl restart pump-bot
```

---

## 10. Cómo añadir o quitar wallets

**Bloquear un dev (que no se le compre nunca):**
1. Abre `blacklist_devs.json`, añade su wallet a la lista `"wallets"`.
2. Reinicia: `sudo systemctl restart pump-bot`.

**Añadir un dev bueno para snipear:**
1. Mete su wallet en el `top_devs_*.json` de la estrategia que toque.
2. Reinicia.

---

## 11. Ajustes de cada bot (dentro de `bots/*.yaml`)

Lo que se suele tocar:
- `buy_amount` → cuántos SOL por compra.
- `buy_slippage` / `sell_slippage` → tolerancia de precio (mayhem usa venta 90% para no fallar).
- `take_profit_*` / `tp2_*` / `tp3_*` → la escalera de venta.
- `stop_loss_percentage` → dónde corta pérdidas.
- `bro_address` → qué lista de devs usa.
- `enabled: true/false` → encender o apagar ese bot.
- `require_mayhem: true` → comprar solo monedas mayhem.

Después de cambiar un `.yaml`, **reinicia** para que lo coja.

---

## 12. Resumen mental

```
Dev bueno lanza moneda  ->  bot la ve (Helius/pumpportal)  ->  ¿está en mi lista y no vetado?
   ->  COMPRA rápido (~$4-5K MC)  ->  vende por tramos + stop-loss + trailing
   ->  si un dev te hace perder 2 veces, se bloquea solo.
Todo corre 24/7 en el VPS, con RPC de pago de Helius.
```

**Y lo más importante, bro:** mide el **P&L neto real** unos días. Si gana, se escala con
cabeza. Si pierde, se apaga sin drama. Nada de meterle más SOL a algo que sangra. 🫡
