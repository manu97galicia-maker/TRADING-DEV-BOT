# Salida por inactividad y avisos de compra

`trade.sin_volumen_bajo_entrada_seg: 300` añade una salida por inactividad a las
cuatro fuentes. Cero la desactiva. No cambia las entradas, el presupuesto del
20%, las tres plazas, el SL del 25%, los TP ni las pausas por pérdidas.

La posición debe tener al menos cinco minutos, la cotización actual debe estar
por debajo de su precio de compra y el nodo debe confirmar que no ha habido
actividad en su curva durante esos cinco minutos. Se consulta la operación más
reciente mediante
[`getSignaturesForAddress`](https://solana.com/docs/rpc/http/getsignaturesforaddress).
Por prudencia, cualquier transacción correcta contra la curva reinicia el plazo,
aunque no sea un swap. Una respuesta vacía, sin fecha, con error, o un RPC caído
no demuestra volumen cero. Las curvas migradas se excluyen de esta nueva regla:
su dirección anterior ya no representa el mercado activo. Mantienen sus TP/SL.

Las consultas se ejecutan en la tarea de auditoría, como máximo cada 15 segundos
por posición, con tres segundos de plazo por comprobación. Nunca bloquean el
monitor de TP/SL. Solo se acepta evidencia con una antigüedad máxima de dos
segundos y se comprueba de nuevo que el precio siga bajo la entrada. Por tanto,
los cinco minutos no implican una venta en el segundo exacto 300. La salida vende
todo el remanente y registra `muerta_sin_volumen`, la prueba y el motivo de
reintento en el libro existente. Una vez iniciada, continúa intentando cerrar el
remanente. La regla de ventas de 1 SOL sigue desactivada.

El plazo se configura en `trade`, junto al resto de parámetros del monitor.
No modifica el fingerprint de las tres políticas de entrada de `four_strategies`
ni exige borrar o sustituir el libro durable. Al reiniciar, también se aplica a
posiciones anteriores; sus fechas y precios se conservan.

Cada compra confirmada genera un único aviso de compra del bundle por Telegram
con nombre, ticker, CA, estrategia, evidencia observada, precio, importe estimado
del fill y los tres TP calculados para esa posición. El nombre se obtiene de los
datos existentes o de una lectura opcional
[`getAsset`](https://www.helius.dev/docs/api-reference/das/getasset), con dos
segundos de plazo. La consulta y el aviso son tareas de fondo: los monitores de
salida empiezan sin esperarlos. Si faltan metadatos, se indica «Nombre no
disponible» y se mantiene el CA. Los textos recibidos se escapan para Telegram.

Se usan `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` del entorno aislado existente.
El envío es best effort: tener credenciales no prueba que Telegram haya entregado
un mensaje. No se envían posiciones antiguas como si fueran compras nuevas.
Los callouts públicos siguen requiriendo una sesión autenticada; un aviso privado
no implica que se haya publicado en Pump.fun.

Instalar esta revisión no recarga un proceso Python que ya está ejecutándose.
El proceso anterior conserva su configuración en memoria. Hace falta un reinicio
manual para activar la nueva regla y los avisos, sin eliminar el libro ni abrir
otra copia simultánea del bot. No se inicia trading durante las pruebas.

## Reinicio después de ventas manuales

El monitor anterior conserva ocupada una plaza si los tokens desaparecen por una
venta externa cuya transacción no conoce. No se debe borrar ese registro ni
poner un resultado inventado para liberar la plaza.

`reconcile_four_manual_sales.py`, sin argumentos, prepara una auditoría de lectura.
Busca el historial de la cuenta de tokens identificada en la compra original,
exige llegar hasta esa compra y comprueba las cantidades y los cambios de SOL
de las transacciones finalizadas. Admite recibos de versión 1 utilizados por
Phantom. Rechaza adquisiciones adicionales, transferencias sin pago nativo,
operaciones que cambian varios activos y datos incompletos. Las posiciones que
conservan tokens permanecen abiertas. No importa ni utiliza una clave firmante.

`--apply` exige adquirir el mismo bloqueo de proceso que el trader; con el bot
activo se niega a modificar el libro. Registra cada venta externa y su cierre
en una sola transacción de SQLite, con el P&L atribuido a los cambios de SOL de
la wallet. Una discrepancia revierte también los recibos añadidos. Se mantienen
las estadísticas y la pausa de tres pérdidas en los últimos cinco cierres.

El siguiente comando es de **ejecución manual** y arranca operaciones reales:

```bash
cd /root/ai-bot-four-strategies
bash reiniciar_four_strategies.sh --real
```

Primero comprueba las ventas sin modificar el libro. Identifica la única instancia
de esta carpeta, solicita su terminación y espera hasta 30 segundos. Con el bot
detenido repite y aplica la conciliación, y solo entonces solicita el nuevo
arranque. Cualquier fallo anterior impide iniciar una segunda copia. El arranque
tiene además su preflight y bloqueo habituales; «arranque solicitado» no significa
que ya haya completado esas verificaciones. El log está en
`logs/four-strategies.log`. No hay corte forzoso a la hora: los monitores de salida
deben seguir atendiendo las posiciones abiertas.
