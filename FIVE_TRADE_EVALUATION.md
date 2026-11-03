# Tandas de cinco por estrategia y cuatro posiciones compartidas

La configuración `five_trade_evaluation`, fuera de `four_strategies`, prepara
tandas independientes de **cinco operaciones auditadas por estrategia**. El
límite global pasa a **cuatro posiciones**, incluidas compras pendientes,
envíos inciertos y remanentes. El presupuesto de cada nueva operación es el
**90% del SOL líquido confirmado para las cinco estrategias**, incluidos márgenes
y gastos. El límite de plazas no permite exceder el saldo ni la reserva de
0,03 SOL. Cada entrada se recalcula sobre el saldo restante; si el presupuesto
no deja esa reserva o coincide con otros gastos pendientes, se rechaza la compra.
Las posiciones previas conservan sus presupuestos registrados.

El SL activo solicitado es **22%** para las cinco estrategias. Para las cuatro
originales se toma de `trade.stop_loss_percentage`; para la quinta también se
guarda en `configs/second-block-dev.yaml`. Al reiniciar, los monitores limitan
también los stops antiguos al 22%, conservando cualquier stop previo más estricto.
La base `four_strategies.exit.stop_loss_fraction: 0.25` permanece solo dentro de
la identidad histórica del libro: no es el SL activo ni requiere borrar o migrar
posiciones, resultados, tandas o pausas. No debe editarse esa base histórica.

Una estrategia puede admitir como máximo cinco operaciones en su tanda. La
sexta espera hasta que las cinco tengan cierre completo, cero tokens verificado
y recibos finalizados. Entonces:

- ROI neto estrictamente positivo: la siguiente entrada abre otra tanda de cinco.
- ROI neto cero o negativo: se pausan sus nuevas entradas de forma persistente.
- Costes sin verificar: se bloquea la evaluación y el ROI permanece desconocido.

El ciclo positivo se repite dentro del programa mientras esté ejecutándose y
las demás protecciones permitan nuevas entradas. Una estrategia pausada no
desactiva los monitores de salida de sus posiciones ni las otras estrategias.

El ROI se calcula en **SOL** como `suma del P&L neto / suma de los débitos reales
de compra`. Los importes proceden de los cambios de saldo nativo de los recibos
finalizados: incluyen las comisiones y la renta, y el resultado contempla también
ventas fallidas y devoluciones de renta. No se usa el presupuesto reservado, el
precio estimado, una venta parcial ni la media simple de porcentajes de trades.
Los intentos fallidos que llegaron a cadena y pagaron gastos consumen una prueba
y registran su pérdida. Una cancelación demostrada antes de enviarse libera la
prueba; un envío incierto conserva su plaza y no se cuenta como victoria.

La regla activa es `legacy_loss_guard: batch_roi_only`: importa la suma neta de
los cinco cierres de cada estrategia. Dos ganadoras pueden compensar tres
perdedoras y permitir otra tanda si, después de gastos, dejan beneficio neto.
El número de pérdidas por sí solo no pausa una estrategia. Un resultado neto
cero o negativo al completar los cinco cierres sí pausa sus nuevas entradas.

Las antiguas pausas por tres pérdidas se conservan como registros históricos,
pero dejan de bloquear entradas con esta política. Las pausas de la evaluación
por ROI no positivo o costes sin verificar siguen activas. Las posiciones y
resultados previos se conservan, pero no se convierten en nuevas pruebas.

Las tablas `five_trade_settings`, `five_trade_batches` y `five_trade_entries`
se crean al iniciar el código o aplicar una conciliación manual. La reserva de
plaza y la asignación de tanda son una sola transacción; el cierre, el P&L y la
evaluación también. El identificador de evaluación y sus tandas sobreviven a
los reinicios. Cambiar una política ya iniciada requiere una migración explícita:
reiniciar o cambiar un flag no borra pérdidas ni permite saltarse una tanda.

Diagnóstico de solo lectura:

```bash
cd /root/ai-bot-four-strategies
.venv/bin/python inspect_five_trade_evaluation.py
```

Si las tablas todavía no existen, informa `not_started` y ROI desconocido. No
crea el libro, no lo modifica, no importa wallets y no inicia operaciones.

El proceso existente no recarga estos cambios desde disco. El siguiente arranque
es de **ejecución manual** y opera con fondos reales:

```bash
cd /root/ai-bot-four-strategies
sed -i 's/^enabled: false$/enabled: true/' configs/second-block-dev.yaml
bash reiniciar_four_strategies.sh --real
```

El segundo comando activa la quinta en su archivo. El tercero concilia las
ventas, detiene la instancia anterior y solicita el nuevo arranque, conservando
el libro y los registros de pausas; las pausas históricas de tres pérdidas ya
no se aplican. Las pruebas de desarrollo usan recibos simulados; no son
cinco trades reales ni evidencia de rentabilidad futura.
