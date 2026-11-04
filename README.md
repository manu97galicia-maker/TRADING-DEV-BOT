# Definitivo Bot — señales tempranas de pump.fun en Solana

## Paquete de cuatro estrategias y callouts

La configuración [configs/four-strategies.yaml](configs/four-strategies.yaml)
reúne presión desde 8K MC, cruce de 21K, primer bloque con al menos dos
compradores y señales de Telegram.
Comparte hasta nueve posiciones y un SL del 22%. Las cuatro estrategias
originales usan el 90% del SOL líquido por entrada; el modo
[solo dev](DEV_ONLY.md) usa el menor entre el 80% y 30 USD, incluidos gastos
estimados. La estrategia dev entra sin esperar tandas de cinco; las tandas históricas
siguen registradas y las otras estrategias conservan su evaluación.
La presión usa 12 compradores y 1.300 USD/8s; la salida por volumen vendedor
está desactivada. Este paquete está **deshabilitado** (`enabled: false`).

La admisión compartida exige historial verificable del creador: al menos una
migración y una tasa de migraciones del 1% o superior. Si la consulta falla o
caduca, no se reserva saldo ni se firma. Cada cierre verificado conserva este
historial y el informe compara ganadoras y no ganadoras por separado para las
cuatro estrategias. El aprendizaje es descriptivo: con menos de 30 cierres por
estrategia solo recopila datos y nunca cambia parámetros automáticamente.

Las reglas, TP escalonados y preparación del publicador @AImemebot están en
[la guía del paquete](estudios/four_strategies/README.md). Validar la configuración
con `python run_four_strategies.py` no carga la wallet ni inicia operaciones.

Los apartados siguientes describen las configuraciones anteriores del repositorio.

Sistema de detección y gestión automática de oportunidades de **pump.fun**. La
estrategia principal de señales combina eventos recientes de DexScreener con
canales seleccionados de Telegram (MoonTok, DEXTools y Legends Alpha), registra
el motivo exacto de cada entrada y simula una salida escalonada con protección
progresiva del capital.

> **DINERO REAL ACTIVADO.** `paper_trade: false` y `max_real_buys: 1`: la
> estrategia compra con SOL de verdad, **una sola vez**, por un máximo de
> 0,20 SOL. El tope está en 1 a propósito — el camino de compra de mints
> existentes nunca se había ejecutado contra una compra real.
>
> Para volver a papel: `bash volver_a_papel.sh`. Para parar del todo:
> `systemctl stop pump-bot`.
>
> `bot-paper-ai-entry` es la **única** estrategia con `enabled: true`; el resto
> de los YAML de `bots/` están desactivados. El runner carga todo
> `bots/*.yaml`, así que reactivar cualquier otro lo pone a operar en paralelo
> y compartiendo wallet.

## Qué hace la estrategia principal

La configuración de referencia está en
[`bots/bot-paper-ai-entry.yaml`](bots/bot-paper-ai-entry.yaml) y se inicia con
[`run_paper_ai_entry.py`](run_paper_ai_entry.py).

Flujo de una señal:

1. Detecta un pago, listado o boost reciente en DexScreener, ve una moneda
   **entrar** en la lista de tendencias de Birdeye, o recibe una señal de uno
   de los canales de Telegram permitidos.
2. Descarta señales antiguas y crea una referencia inicial al arrancar para no
   confundir eventos históricos con eventos nuevos.
3. Filtra por origen en tres niveles:
   - **Solo curva de pump.fun** (lo más estrecho).
   - `incluir_migradas: true` — añade las de pump.fun que ya pasaron a
     PumpSwap o Raydium; se entra y se sale por el pool. En esas **no hay
     curva**, así que el anti-rug en vivo y `min_curve_sol` no las protegen:
     queda el stop y el cierre por tiempo.
   - `incluir_cualquier_origen: true` — añade monedas que **nunca** fueron de
     pump.fun. Ahí tampoco hay creador ni historial de dev, el supply no es el
     fijo de pump.fun, y aparecen riesgos que en pump.fun no existen (autoridad
     de congelación, comisión por transferencia, supply mintable). Por eso al
     activarlo se consulta **RugCheck** y se exige `min_lp_locked_pct` de
     liquidez bloqueada; sin informe, la moneda se descarta.
4. Rechaza tokens `mayhem` y no usa LetsBonk para esta estrategia.
5. Asigna el importe según el motivo exacto de la señal.
6. En paper trading, simula la entrada, sigue el precio y ejecuta la escalera de
   take-profit y stop-loss.
7. Envía a Telegram el motivo, importe, precio, contrato, objetivos y enlaces
   directos a DexScreener, GMGN y pump.fun.
8. Si el resultado identifica un rug del desarrollador, lo añade al bloqueo
   tras el primer caso configurado.

### Fuentes autorizadas

| Fuente | Tipo de evento | Estado en la estrategia |
|---|---|---|
| DexScreener | paid/listing y boosts | Activada por API pública |
| MoonTok / `CryptoListings` | listado o promoción | **Desactivada** (fuera de la lista de canales) |
| DEXTools / `dextools_nitro` | Nitro | Configurada por Telegram |
| `DEXTNewPairsBot` | nuevo par | **Desactivada** (fuera de la lista de canales) |
| `LegendsAlphas` | call publicado | **Desactivada** (fuera de la lista de canales) |
| GMGN | enlace informativo en avisos | Las señales de compra GMGN están desactivadas |
| Birdeye | entrada en la lista de tendencias | Implementada pero **desactivada**: es atención, no pago |
| API DEXTools | datos de mercado auxiliares | Opcional, no dispara esta estrategia |
| Discord u otras estrategias | señales adicionales | Desactivadas |

La fuente de Birdeye está escrita y probada pero viene **desactivada**. Dispara
cuando una moneda **entra** en la lista de tendencias, que es una señal de
atención, no de que nadie haya pagado por promocionarse. Birdeye sí cobra por
marketing (Token Info Update Service, 200–300 $), pero esa verificación tarda
2–7 días hábiles y no expone ningún endpoint de "quién acaba de pagar", así
que no sirve como equivalente del paid de DexScreener. Además la mayoría de
las tendencias ya migraron fuera de la curva y se descartan solas; el
heartbeat separa "entradas nuevas" de "comprables en curva" para que se vea
cuántas sobreviven. Se activa con `enabled: true` en el YAML.

El lector de Telegram necesita una sesión Telethon autorizada. Configurar los
canales no garantiza que se reciban mensajes si falta completar el inicio de
sesión de Telegram en el servidor.

## Tamaño de entrada

| Motivo detectado | Importe configurado |
|---|---:|
| Listing/paid normal de DexScreener o MoonTok | 0,15 SOL |
| Boost pequeño o DEXTools Nitro pequeño | 0,17 SOL |
| Boost/Nitro de 500 o superior | 0,20 SOL |
| Entrada en tendencias de Birdeye | 0,15 SOL |

Controles adicionales:

- Tope absoluto por compra: **0,20 SOL**.
- Reserva mínima que no se debe gastar: **0,03 SOL**.
- Máximo de posiciones simultáneas: **3**.
- Slippage de compra: **20 %**; slippage de venta: **25 %**.
- Fee de prioridad fijo: `700000`, con `140000` compute units para compra y
  `120000` para venta. El coste final en USD depende del precio de SOL y de la
  transacción; no se debe presentar como una cifra fija en dólares.

Los importes son tamaños de estrategia, no una promesa de ejecución ni de
rentabilidad. En modo paper no sale SOL de la cartera.

## Salida, take-profit y protección del stop

La posición se divide en cuatro tramos:

| Escalón | Precio objetivo | Porcentaje vendido | Nuevo stop protegido |
|---|---:|---:|---:|
| Inicial | — | — | -18 % desde la entrada |
| TP1 | +25 % | 70 % | Precio de entrada (break-even) |
| TP2 | +50 % | 10 % | Nivel de TP1 (+25 %) |
| TP3 | +75 % | 10 % | Nivel de TP2 (+50 %) |
| TP4 | x3 (+200 %) | 10 % restante | Cierre total |

Además hay dos salidas por falta de recorrido, ambas a los **3 minutos**:

- **Sin movimiento** (`venta_muerta_seg: 180`, `venta_muerta_gain: 0.02`): a los
  3 minutos se mira una sola vez; si no ha hecho al menos un +2 %, se vende
  entera. Una moneda con volumen de verdad se mueve. Si a esa altura va por
  encima, se la deja correr y a partir de ahí mandan los take-profit.
- **Por debajo de la compra** (`venta_bajo_entrada_seg: 180`): 3 minutos
  seguidos por debajo del precio de entrada y sale entera, moonbag incluido.
  El contador se reinicia en cuanto recupera la entrada, así que exige 180
  segundos sin levantar cabeza, no 180 sueltos.

El stop queda siempre un peldaño por detrás del último take-profit alcanzado.
El cálculo está aislado en `src/trading/tiered_exit.py` y cubierto por pruebas
de regresión en `tests/test_tiered_stop.py`.

## Avisos de Telegram

Los avisos de entrada incluyen:

- Nombre y símbolo del token (el nombre real sale del feed de DexScreener).
- Contract address (mint).
- Motivo exacto: listing, paid, boost, Nitro o canal de origen.
- Cantidad de SOL asignada y precio de entrada.
- Escalera completa de TP/SL.
- Enlaces directos a DexScreener, GMGN y pump.fun.

Además, **en el momento exacto en que salta cada peldaño** llega un aviso
propio (no hay que esperar al informe final):

- Nombre y ticker de la moneda.
- Qué saltó: `TP1`…`TPn` o `STOP`.
- El motivo por el que se compró (listing, paid, boost, Nitro o canal).
- Precio del disparo y movimiento desde la entrada.
- Porcentaje que vende ese peldaño y cuánto queda abierto.
- **P&L de la posición en ese instante**, en SOL y en porcentaje, contando lo
  ya cobrado en peldaños anteriores más lo que vale el resto.
- Adónde sube el SL protegido (en los TP).
- Enlaces a DexScreener, GMGN y pump.fun, y el contract address.

Los avisos de cierre añaden los take-profits alcanzados, porcentaje vendido,
precio exacto de cada venta simulada, precio medio vendido, último precio
observado, SL inicial/final, porcentaje restante, ROI, P&L y win rate acumulado.

Hay dos conexiones distintas con Telegram:

- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`: envío de notificaciones.
- `TELEGRAM_API_ID` + `TELEGRAM_API_HASH`: lectura de canales mediante
  Telethon; requiere autorizar una sesión con el número de teléfono y el código
  que envía Telegram.

Nunca pegues tokens, códigos de acceso, frases semilla ni claves privadas en un
issue, commit, chat o captura de pantalla. Si una credencial se comparte por
error, hay que revocarla y crear otra.

### Datos para ajustar TP y SL

Cada observación terminada se guarda en `paper_trades/results.jsonl` con:

- Precio de compra, último precio y precio medio efectivo de venta.
- Ventas individuales por TP/stop, con precio y porcentaje de posición.
- SL inicial, SL protegido final y precio ejecutado por stop.
- Número de TPs alcanzados, porcentaje vendido y porcentaje restante.
- Pico favorable, caída máxima, ROI, P&L y resultado win/loss.
- Fuente exacta de la señal y nivel de boost/Nitro.

Estos datos permiten ajustar después los objetivos con medianas reales de la
muestra, sin cambiar la estrategia por una sola moneda. El resumen se obtiene
sin acceder a la wallet:

```bash
uv run learning-examples/summarize_signal_results.py
```

El informe calcula win rate, tasa de stops, mediana de ROI, mediana del pico
(referencia para TP), mediana de la caída (referencia para SL), mediana de la
salida por stop y mediana de escalones TP alcanzados. Conviene esperar una
muestra suficiente y separar por fuente antes de modificar los niveles.

## Callouts y redes sociales

La configuración genera el texto:

```text
AI opportunity detected: <nombre de la moneda>
Contract address: <mint>
Disclosure: this automated account holds a position in this token.
Not financial advice.
```

Actualmente `callouts.publish` está en `false`: el mensaje se guarda en
`trades/pumpfun_callout_outbox.jsonl`, pero **no se publica automáticamente** en
pump.fun. Tampoco hay publicación automática en X/Twitter ni una cuenta de X
conectada al bot.

**Pump.fun admite un callout cada 6 horas por cuenta** (cuatro al día) y desde
agosto reparte las recompensas primando calidad sobre cantidad, así que vaciar
la cola no serviría: de cada ventana solo cabe uno.
`callout_outbox.elegir_para_publicar()` resuelve esa parte — devuelve el
candidato de mayor señal entre los pendientes, o `None` si la ventana sigue
ocupada.

Lo que falta es el envío en sí. Pump.fun no documenta una API pública de
escritura para callouts (es una función de la app móvil), así que publicar
requiere una credencial que no vive en este repositorio, protección contra
duplicados y revisar los términos de Callout Rewards.

## Frescura y deduplicación

Para evitar compras motivadas por volumen viejo:

- DexScreener se consulta cada 30 segundos.
- Un evento paid/boost puede tener como máximo 10 minutos.
- El token puede tener como máximo 30 minutos para esta fuente.
- Al arrancar se toma una línea base y no se emiten como nuevas las señales ya
  existentes.
- Se envía un heartbeat de monitorización cada 5 minutos.

## Arquitectura

```text
bots/
  bot-paper-ai-entry.yaml      estrategia y límites de seguridad
src/
  bot_runner.py                carga YAML y construye el trader
  monitoring/dexpaid_source.py eventos paid/boost de DexScreener
  monitoring/birdeye_source.py  entradas nuevas en tendencias de Birdeye
  monitoring/telegram_source.py lectura de MoonTok/DEXTools/Legends
  monitoring/notifier.py       mensajes de entrada, TP y SL
  monitoring/callout_outbox.py cola local de callouts
  dexscreener/                 cliente y filtros de DexScreener/RugCheck
  trading/universal_trader.py  filtros, entrada y gestión de posición
  trading/tiered_exit.py       escalera de TP y stop progresivo
  paper_trading/               simulación y persistencia paper
  dashboard/                   panel y utilidades de wallet
learning-examples/             pruebas manuales y ejemplos sin producción
tests/                         pruebas automáticas
pump-bot.service         servicio systemd del modo paper
```

El repositorio conserva estrategias históricas en otros YAML y servicios.
No forman parte de `bot-paper-ai-entry` y no deben arrancarse a la vez por
accidente, especialmente si comparten wallet.

## Instalación local

Requisitos:

- Python 3.10 o superior.
- [`uv`](https://docs.astral.sh/uv/).
- Un endpoint RPC/WSS de Solana.
- Credenciales propias para las integraciones opcionales.

```bash
uv sync
cp .env.example .env
```

Variables principales de `.env`:

```dotenv
SOLANA_NODE_RPC_ENDPOINT=https://...
SOLANA_NODE_WSS_ENDPOINT=wss://...
SOLANA_PRIVATE_KEY=...

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...

BIRDEYE_API_KEY=...
DEXTOOLS_API_KEY=...
DEXTOOLS_API_TIER=trial
```

`.env` está ignorado por Git. La clave de la wallet debe guardarse únicamente
en el servidor o gestor de secretos que ejecuta el bot, con permisos mínimos.

No se configura una dirección: se configura la **clave**, y la dirección se
deriva de ella. Para confirmar con qué wallet va a operar el bot antes de que
gaste nada —sin exponer la clave, que este script nunca imprime—:

```bash
uv run learning-examples/comprobar_wallet.py
uv run learning-examples/comprobar_wallet.py --esperada <direccion>
```

Con `--esperada` compara la dirección derivada con la que se espera y avisa si
no coinciden. También muestra el saldo y cuánto queda disponible descontando
`min_reserve_sol`.

## Verificación segura

Las normas del proyecto exigen validar primero los ejemplos de aprendizaje y
paper trading; una sesión de desarrollo no debe operar fondos reales.

```bash
uv run learning-examples/comprobar_wallet.py
uv run learning-examples/fetch_price.py
uv run learning-examples/check_market_data_apis.py
uv run learning-examples/preview_purchase_notification.py
uv run learning-examples/simular_avisos_telegram.py
uv run pytest
uv run ruff format --check .
uv run ruff check .
```

Arranque local de la estrategia paper:

```bash
uv run run_paper_ai_entry.py
```

Para ver cómo quedan los avisos sin esperar a una señal real,
`simular_avisos_telegram.py` recorre dos posiciones completas —una que cobra
tres peldaños y sale por el stop protegido, y otra que se va directa al stop—
usando las mismas funciones que el bot y leyendo la escalera del YAML. Sin
argumentos solo imprime; con `--enviar` los manda al Telegram configurado:

```bash
uv run learning-examples/simular_avisos_telegram.py --enviar
```

Antes de interpretar que Telegram “falla”, comprobar por separado el envío del
bot y la autorización de la sesión lectora:

```bash
uv run learning-examples/check_telegram_session.py
```

Si responde `False`, la sesión lectora no está autorizada y los canales no
entran aunque estén configurados. Se autoriza una sola vez, desde la raíz del
proyecto (ahí es donde el bot busca el fichero de sesión):

```bash
uv run learning-examples/autorizar_telegram.py
```

Pide el número con prefijo internacional y el código que envía Telegram. Son
credenciales de `my.telegram.org` (`TELEGRAM_API_ID` / `TELEGRAM_API_HASH`),
distintas del token de BotFather que sirve para enviar avisos.

## Despliegue en un droplet

El servicio incluido asume `/root/ai-bot` como directorio de trabajo y una
virtualenv en `/root/ai-bot/.venv`.

```bash
sudo cp pump-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pump-bot
sudo systemctl status pump-bot --no-pager
sudo journalctl -u pump-bot -f
```

Después de cada cambio:

1. Subir únicamente los archivos revisados.
2. Sincronizar dependencias si cambió `pyproject.toml` o `uv.lock`.
3. Verificar que la configuración sigue en paper.
4. Reiniciar `pump-bot`.
5. Confirmar en logs que no hay otro servicio de trading activo.
6. Revisar que Telegram envía heartbeat y avisos nuevos, no señales antiguas.

No se incluyen IP, claves SSH ni credenciales del droplet en este documento.

## Checklist antes de plantear operación real

**Activación real pendiente:** se está creando una wallet nueva. La wallet cuya
clave se compartió anteriormente se considera comprometida y no debe utilizarse
para operar. Hasta introducir la nueva clave únicamente en el `.env` privado
del servidor y completar este checklist, el servicio seguirá en paper trading.

- [ ] Nueva wallet dedicada y financiada solo con el límite aceptado.
- [ ] Clave privada nunca compartida y fuera de Git.
- [ ] Token de BotFather, API hash de Telegram y token RPC rotados si alguna vez
      fueron expuestos.
- [ ] Sesión lectora de Telegram autorizada en el droplet.
- [ ] Varias sesiones paper revisadas con precios y resultados verificables.
- [ ] Señales antiguas y duplicadas descartadas correctamente.
- [ ] Reserva de 0,03 SOL y tope de 0,20 SOL probados.
- [ ] Stop escalonado probado para TP1, TP2, TP3 y TP4.
- [ ] Un solo servicio de estrategia habilitado.
- [ ] Callouts y redes sociales revisados por separado.
- [ ] Activación real mediante un cambio explícito, pequeño y reversible.

## Seguridad del repositorio

- El repositorio debe mantenerse privado.
- No se deben versionar `.env`, sesiones de Telegram, logs, bases de datos paper
  con datos sensibles, claves SSH, frases semilla ni archivos de wallet.
- Las invitaciones de colaboradores conceden acceso al código privado: revisar
  periódicamente colaboradores, invitaciones pendientes y deploy keys.
- Activar alertas de dependencias y actualizaciones de seguridad en GitHub.
- Ante una filtración, borrar el secreto del historial no basta: hay que
  revocarlo o mover los fondos a una wallet nueva.

## Limitaciones importantes

- Paper trading no reproduce con exactitud slippage, congestión, prioridad ni
  fallos de transacción reales.
- Un paid listing o un boost es una señal de marketing, no una garantía de
  legitimidad ni de subida.
- El bloqueo por desarrollador reduce repetición de rugs conocidos, pero no
  identifica por sí solo wallets nuevas del mismo actor.
- DexScreener, Telegram, GMGN y los RPC pueden sufrir retrasos o caídas.
- No existe una forma de garantizar que una estrategia de memecoins no pierda
  dinero.

## Documentación adicional

- [`AGENTS.md`](AGENTS.md): normas obligatorias de desarrollo seguro.
- [`ESTILOS_DE_RUG.md`](ESTILOS_DE_RUG.md): patrones de rug observados.
- [`PAPER_TRADING.md`](PAPER_TRADING.md): sistema de simulación.
- [`GUIA_BOTS.md`](GUIA_BOTS.md): estructura de bots y servicios.
- [`CLAUDE.md`](CLAUDE.md): notas técnicas del protocolo y del repositorio.

## Licencia

Consulta [`LICENSE`](LICENSE).
