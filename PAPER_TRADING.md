# Paper trading seguro

`bots/bot-paper-dev-history.yaml` activa un flujo separado que **no construye ni
firma transacciones**. Además, `real_trading: true` produce un error al arrancar.
El perfil usa la lista híbrida `top_devs_gemas_mas.json`, banca simulada inicial
de 2 SOL y entradas fijas de 0,25 SOL (máximo teórico: 8 simultáneas sin contar
el PnL acumulado).

## Señal

La señal predeterminada exige simultáneamente MC actual `< $4.000`, media de los
picos históricos del dev `> $12.000` y al menos 5 lanzamientos anteriores. Cada
mint tiene clave primaria en SQLite, por lo que reinicios y eventos repetidos no
duplican una señal.

El perfil activo arranca prospectivamente con la fotografía agregada
`top_devs_gemas_mas.json`: exige entrada con MC `< $2.750`, `median_peak`
`> $10.000` y 5 lanzamientos. La métrica se etiqueta expresamente como
**mediana histórica** y no se presenta como una media.

Cuando haya exportación granular, el historial puede cambiarse a
`paper_trades/dev_history.jsonl`, una fila por lanzamiento:

```json
{"dev":"wallet","mint":"CA","symbol":"ABC","launched_at":1786000000,"peak_mc":18400}
```

`launched_at` es Unix UTC. Al evaluar una señal solo se leen filas con fecha
estrictamente anterior y se excluye el mint actual. Para un backtest limpio,
`peak_mc` debe ser el pico alcanzado dentro de una ventana histórica fija y ya
cerrada (por ejemplo, 30 minutos), nunca el máximo de toda la vida calculado con
datos posteriores al momento de decisión.

## Datos y avisos

La base `paper_trades/dev_history.sqlite3` guarda entrada, dev, media, número de
lanzamientos, mejor lanzamiento, variables opcionales y resultado. Se registran
retornos a 1/3/5/10/30 minutos, MFE, MAE, primer contacto y tiempo de cada pareja
TP/SL configurada. Telegram recibe entrada, cierre y resúmenes horario/diario.

## Optimizador temporal

Ejecutar después de acumular operaciones cerradas:

```bash
uv run python -m paper_trading.optimizer --grid paper_optimizer_grid.json
```

Las observaciones se ordenan por fecha; el 70% más antiguo es train y el 30% más
nuevo es test. Los parámetros se eligen solo en train. La puntuación prioriza EV,
penaliza dispersión y drawdown; el win rate solo se informa. Buyers, volumen,
velocidad, bundle, snipers, concentración y dev holding se filtran cuando el feed
los haya guardado; una variable ausente no se inventa ni se interpreta como cero.

## Puesta en marcha

1. Exportar el historial point-in-time al JSONL indicado.
2. Reutilizar el `.env` del bot con `SOLANA_NODE_RPC_ENDPOINT`,
   `SOLANA_NODE_WSS_ENDPOINT` y Telegram. La clave se sigue exigiendo por
   compatibilidad con el runner, pero este flujo nunca la usa para operar.
3. Arrancar el perfil, que ya está habilitado en modo exclusivamente simulado.
4. Verificar entradas/cierres en Telegram y consultar SQLite antes de optimizar.
