# Instrucciones para Claude: bot BSC Four.meme + Flap

## Objetivo y límites

Este bot descubre en GMGN lanzamientos de BNB Smart Chain procedentes
exclusivamente de Four.meme y Flap. La ruta del repositorio en el droplet es:

```text
/root/ai-bot
```

Claude no debe pegar, imprimir, registrar ni versionar claves privadas. Tampoco
debe activar operaciones reales sin una orden explícita del propietario y sin
haber completado primero el preflight y la validación paper descritos aquí.

## Estrategia activa

- Compra: 25 USD por entrada.
- Market cap de entrada: estrictamente inferior a 9.000 USD.
- Historial del creador: mínimo 2 migraciones y ratio migrado mínimo del 8%.
- Impuesto del token: compra + venta no puede superar el 6%.
- Nombres: rechaza otro lanzamiento con el mismo nombre normalizado durante 2 h.
- Posiciones: máximo 2 abiertas simultáneamente. No existe límite total de
  operaciones cerradas.
- Reserva: después de la compra y del máximo de 1,30 USD reservado para gas,
  deben quedar al menos 5 USD en BNB.
- Stop loss: -38% desde el precio ejecutado.
- Al subir 60%: vende 62,5% de los tokens originales para recuperar el capital
  inicial antes de comisiones.
- Vende otro 27,5% repartido al alcanzar MC de 10k, 15k, 20k, 25k, 30k y 35k.
- Conserva 10%: vende 5% en 180k, 2,5% en 400k y 2,5% en 900k.

Las salidas y posiciones se guardan de forma persistente para evitar repetir
escalones después de reiniciar.

## Archivos y rutas

| Función | Ruta en el droplet |
|---|---|
| Arranque | `/root/ai-bot/run_bsc.py` |
| Config paper | `/root/ai-bot/bots/bot-bsc-four-flap-paper.yaml` |
| Plantilla real | `/root/ai-bot/bots/bot-bsc-four-flap-real.yaml` |
| Secretos | `/root/ai-bot/.env` |
| Estado paper | `/root/ai-bot/reservas/bsc_positions_paper.json` |
| Estado real | `/root/ai-bot/reservas/bsc_positions_real.json` |
| Nombres recientes | `/root/ai-bot/reservas/bsc_recent_names.json` |
| Servicio | `/root/ai-bot/deploy/bsc-four-flap.service` |
| Documentación técnica | `/root/ai-bot/BSC_STRATEGY.md` |

No borrar ni editar manualmente los JSON de estado mientras el proceso esté
activo. No reutilizar el fichero paper como estado real.

## Variables privadas

Crear `/root/ai-bot/.env` con permisos `600`:

```env
BSC_RPC_HTTP=https://RPC-PRIVADO-DE-BSC
GMGN_API_KEY=API-KEY-DE-SOLO-LECTURA
BSC_PRIVATE_KEY=CLAVE-DE-UNA-WALLET-BSC-DEDICADA
BSC_ENABLE_LIVE_TRADING=
```

La clave BSC debe ser la clave privada hexadecimal aceptada por Web3.py, de una
wallet dedicada y con saldo limitado. Nunca usar una seed phrase ni una wallet
principal. La clave API de GMGN se usa únicamente para consultas de mercado.

Aplicar permisos sin mostrar el contenido:

```bash
chmod 600 /root/ai-bot/.env
```

## Validación obligatoria en paper

Desde `/root/ai-bot`:

```bash
cd /root/ai-bot
uv sync
PYTHONPATH=/root/ai-bot/src .venv/bin/python run_bsc.py \
  --config bots/bot-bsc-four-flap-paper.yaml --preflight
```

El preflight es de solo lectura: verifica RPC BSC chain 56, bytecode de los
contratos canónicos y acceso a GMGN. Después ejecutar paper continuamente:

```bash
PYTHONPATH=/root/ai-bot/src .venv/bin/python run_bsc.py \
  --config bots/bot-bsc-four-flap-paper.yaml
```

Antes de considerar real, comprobar durante una muestra suficiente que existen
entradas paper, que nunca hay más de dos abiertas, que se ejecutan SL/TP, que
los impuestos se rechazan correctamente y que reiniciar conserva posiciones y
escalones. Un preflight correcto por sí solo no valida rentabilidad.

## Cómo desbloquear modo real

La plantilla real viene bloqueada intencionadamente. Solo con autorización
explícita, cambiar en `bots/bot-bsc-four-flap-real.yaml`:

```yaml
mode:
  paper_trade: false
  live_transactions: true
```

Después establecer en `/root/ai-bot/.env` la frase exacta:

```env
BSC_ENABLE_LIVE_TRADING=I_UNDERSTAND_BSC_LIVE_TRADING
```

Hay tres puertas independientes: los dos booleanos del YAML, la frase exacta
del entorno y una clave BSC válida. Si cualquiera falla, no se firma.

Ejecutar primero el preflight real, que no compra:

```bash
cd /root/ai-bot
PYTHONPATH=/root/ai-bot/src .venv/bin/python run_bsc.py \
  --config bots/bot-bsc-four-flap-real.yaml --preflight
```

No usar `--once` como prueba: en configuración real puede comprar. La primera
sesión real debe supervisarse directamente, con saldo limitado, comprobando en
BscScan la primera compra y su salida antes de dejar el servicio desatendido.

## Servicio systemd

Instalar el servicio solo después de superar paper y la primera operación real
supervisada:

```bash
cp /root/ai-bot/deploy/bsc-four-flap.service \
  /etc/systemd/system/bsc-four-flap.service
systemctl daemon-reload
systemctl enable --now bsc-four-flap.service
systemctl status bsc-four-flap.service --no-pager
journalctl -u bsc-four-flap.service -n 100 --no-pager
```

Detención inmediata:

```bash
systemctl disable --now bsc-four-flap.service
```

Para volver a modo seguro, detener el servicio, vaciar
`BSC_ENABLE_LIVE_TRADING`, restaurar los booleanos del YAML a paper y volver a
ejecutar el preflight. Nunca cambiar configuración mientras el bot siga activo.

## Diagnóstico sin revelar secretos

Se puede verificar únicamente si las variables existen, nunca imprimir sus
valores. En informes, mostrar solo dirección pública, chain ID, saldo agregado,
hash de transacción y errores saneados. Ante un estado JSON corrupto, error de
GMGN, RPC incorrecto, bytecode ausente, simulación fallida, gas superior al
límite, impuestos desconocidos o recibo revertido, el comportamiento esperado
es no comprar o detenerse; no se debe eliminar la protección para forzar una
operación.
