# Definitivo bot — sniper de pump.fun

Bot de trading automático sobre [pump.fun](https://pump.fun) (Solana). Detecta
lanzamientos nuevos, decide en segundos si comprar y gestiona la salida por
escalones de take-profit, trailing stop y varias salidas de emergencia.

Corre **24/7 con dinero real** en un droplet de DigitalOcean.

> **Aviso.** Esto opera con fondos reales sobre memecoins. La inmensa mayoría de
> los lanzamientos de pump.fun van a cero. Todo lo que hay aquí está calibrado
> con operaciones medidas, y aun así el resultado acumulado del 10-ago-2026 era
> **negativo**. Lee [ESTILOS_DE_RUG.md](ESTILOS_DE_RUG.md) antes de tocar nada.

---

## Cómo está montado

```
src/
  bot_runner.py              arranque: lee el yaml y construye el trader
  trading/universal_trader.py  el cerebro: filtros de entrada y gestión de salida
  trading/platform_aware.py    ejecución de compra/venta contra la curva
  monitoring/volumen.py        contadores en memoria desde los logs del websocket
  monitoring/pumpfun_dev.py    historial de migraciones de un creador
  monitoring/notifier.py       avisos a Telegram
bots/*.yaml                  una estrategia por fichero
run_*.py + *.service         un lanzador y un servicio systemd por estrategia
```

Cada estrategia es **un yaml y un proceso**. Comparten la misma wallet, así que
cada una reserva su propia gasolina (`min_reserve_sol`) para poder firmar sus
ventas aunque las otras estén comprando.

## La estrategia viva

`bots/bot-sniper-premium.yaml`. La entrada son tres condiciones:

| | |
|---|---|
| market cap | $4.000 – $5.500 |
| volumen | ≥40 SOL (~$3.000) |
| tiempo | dentro de los primeros 30 s |

Y once protecciones que no son señal de entrada sino seguro, cada una nacida de
una pérdida concreta — están documentadas una a una en
[ESTILOS_DE_RUG.md](ESTILOS_DE_RUG.md).

Salida:

```
80% al +25%     devuelve el capital entero (0,80 × 1,25 = 1,00)
20% restante    trailing del 33% desde el máximo
stop            25%
```

Simétrico a propósito. La versión anterior arriesgaba un 45% para ganar un 6%,
lo que exigía acertar el 91% de las veces.

## Lo que hay que saber antes de tocar el código

**El P&L que calcula el bot no es el que entra en la cartera.** Durante días
reportó ganancias mientras la wallet perdía: calculaba `tokens × precio
observado`, sin descontar comisión de pump.fun, impacto de la venta ni
slippage. En una operación llegó a equivocarse de **signo**.

Desde entonces se guarda la firma de la compra y de cada venta, y al cerrar se
suma el delta real de la wallet leído de `preBalances`/`postBalances`. Los logs
llevan las dos cifras y la diferencia:

```
S'MORE: P&L REAL -0.0099 SOL (cadena) | el teorico decia +0.0906 -> se equivocaba en -0.1005
```

**Cualquier decisión se toma con el número de la cadena.** El otro sirve para
vigilar cuánto se desvía.

## Herramientas de análisis

| | |
|---|---|
| `estudiar_operaciones.py` | cruza condiciones de entrada con el P&L real y compara ganadoras contra perdedoras |
| `automatizar_estudio.sh` | lanza el estudio cuando hay 8 operaciones nuevas y manda el resumen por Telegram (cron cada 15 min) |
| `simular_salidas.py` | prueba escaleras de salida vela a vela sobre monedas reales |
| `descubrir_devs_cadencia.py` | busca creadores buenos **que además lancen a menudo** |
| `descubrir_devs_200k.py` | creadores con monedas por encima de $200K, sin granjas |
| `construir_devs_premium.py` | filtra la lista orgánica por no-spam y pico mediano |
| `fusionar_devs_buenos.py` | une las listas y quita a los auto-baneados |
| `medir_mc_inicial.py` | market cap de arranque de las monedas de cada dev |

## Puesta en marcha

```bash
uv sync
cp .env.example .env      # rellenar claves; .env NUNCA se commitea
uv run src/bot_runner.py  # o el run_*.py de la estrategia
```

`.env` necesita como mínimo `SOLANA_PRIVATE_KEY`, `SOLANA_NODE_RPC_ENDPOINT` y
`SOLANA_NODE_WSS_ENDPOINT`. Los avisos de Telegram son opcionales
(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

## Despliegue

El servidor **no puede hacer `git pull`**: el repositorio es privado y el
droplet no tiene credenciales de GitHub. El tarball público también da 404. Se
sube por SSH:

```bash
tar czf - bots/ src/ | ssh -i ~/.ssh/id_droplet_bot root@<IP> \
  "cd /root/ai-bot && tar xzf -"
ssh -i ~/.ssh/id_droplet_bot root@<IP> "systemctl restart premium"
```

Los servicios llevan `Restart=always` y `StartLimitIntervalSec=0` — sin esto
último systemd se rinde tras cinco arranques en diez segundos y deja el
servicio en `failed` para siempre, que es lo contrario de lo que quieres en un
bot 24/7. Va en la sección `[Unit]`: puesto en `[Service]` se ignora en
silencio.

**El bot no reanuda posiciones al reiniciar.** Avisa de "bolsa huérfana" y la
deja para vender a mano. Conviene agrupar los cambios en un solo reinicio y
comprobar antes que no hay posiciones abiertas.

## Notas del protocolo

Las trampas de pump.fun (número de cuentas por instrucción, PDAs no
documentadas en el IDL, Token-2022, modo mayhem, cashback) están en
[CLAUDE.md](CLAUDE.md). El IDL incluido está **incompleto**: contrastar siempre
contra una transacción reciente que haya funcionado.

## Licencia

Ver [LICENSE](LICENSE).
