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
