# Entradas únicamente de dev con migraciones verificadas

`--only-dev` admite nuevas entradas solo de `dev-second-block`. Presión
compradora, cruce de 21k, primer bloque de cuatro compradores y señales de
Telegram no abren posiciones en este modo. El colector de Solana permanece
conectado para observar creación, primer bloque, historial del dev y segundo
bloque; no se abre la sesión de señales de Telegram.

Se conservan el mismo libro, bloqueo de proceso, posiciones, tandas y pausas.
Las posiciones antiguas de cualquier estrategia mantienen sus monitores de
salida y auditoría. La selección no borra pérdidas ni reinicia la evaluación.

El perfil activo es `entry_rule: creator_migration_only`, con
`entry_timing: second_produced_block`, `require_dev_initial_buy: true` y
`require_first_block_activity: false`. Exige que el dev firmante tenga al menos
un 1% de sus lanzamientos anteriores migrados a PumpSwap y verificados mediante
Helius. No exige un mínimo de volumen ni dos compradores distintos en el
primer bloque. Los umbrales de 250 USD y dos compradores quedan guardados
para el perfil opcional de actividad, pero no se aplican con este selector.

Cuando Helius devuelve datos incompletos o supera cuatro segundos, el respaldo
GMGN consulta los agregados del dev con `GMGN_API_KEY`. El total se obtiene de
`inner_count + open_count`, no del tamaño de la lista de tokens, que puede
estar truncada. Las migraciones que cuentan para el 1% se contrastan con la
curva, el pool PumpSwap canónico y la firma de creación del mismo dev.
El denominador de ese respaldo es el índice de GMGN, una fuente distinta de
la reconstrucción completa de transacciones de Helius; queda identificado así
en los datos de entrada. Las creaciones nuevas observadas que no aparecen en
la lista se suman conservadoramente. También se suman los tokens que aparecen
solo en la segunda consulta, evitando combinar éxitos nuevos con un total
anterior más pequeño. La moneda actual solo se resta cuando
se demuestra que el índice la incluye. Datos incompletos de ambos proveedores
siguen impidiendo la entrada. El trabajo se realiza fuera del colector y se
reutiliza en caché; la primera consulta de un dev puede añadir latencia.

La primera operación de ese mint dentro de la transacción de creación debe
ser una compra del propio dev (`CreateEvent.user`). Se comprueba con los eventos
de esa misma transacción; el orden de llegada de transacciones al WebSocket no
demuestra quién compró primero en todo el bloque.

El dev se identifica siempre por `CreateEvent.user`, independientemente de quién
reciba las comisiones. Si Pump.fun activa su reparto oficial de comisiones,
`BondingCurve.creator` puede pasar a la PDA `sharing-config` del mismo mint,
derivada bajo el programa Pump Fees. La preparación y la revisión previa a
compra aceptan el destinatario original o esa PDA exacta; otros cambios siguen
rechazados. Antes de construir la compra se actualizan `creator` y su
`creator_vault`, conservando el dev original, la prueba de su compra inicial y
el historial exigido. Los registros guardan `creation_fee_creator` y
`entry_fee_creator` para distinguir ambas identidades.

La curva se autentica con su propietario Pump.fun y su discriminador antes de
decodificarla, utilizando la misma lectura existente. El comprador vuelve a
validar la identidad de comisiones si relee el estado. La compatibilidad no
amplía la ventana del segundo bloque ni exige otra consulta al proveedor.

Solo intenta comprar en el siguiente bloque producido cuyo padre es el bloque
de creación. No se calcula sumando uno al número de slot. La prueba del dev,
el historial y la curva deben estar disponibles mientras siga vigente esa ventana
de 0,6 segundos desde su notificación. Si llegan tarde, pasa al siguiente token,
sin comprar en el tercero ni perseguir una moneda antigua. La comprobación de
slot `processed` y los controles previos a firma y envío impiden continuar con
una ventana que ya se sabe caducada; la red puede incluir una transacción más
tarde y no se garantiza ser la primera compra del segundo bloque.

No exige MC, concentración ni historial de variaciones de precio. No compra
monedas ya migradas a un pool. Desactivar el selector de tiempo conserva la
variante opcional de historial sin bloque fijo; no es el perfil actual.

La preparación consulta tanto la curva como la cuenta del token con
`commitment: processed`, en paralelo, para no esperar la finalización de una
creación recién observada. El colector del perfil de historial no consulta
SQLite por cada candidato y evento: las pausas y el ROI se verifican al entrar
en el manejador y de nuevo en la reserva atómica antes de enviar.
La verificación del historial del dev, una conexión operativa y la inclusión
por la red siguen siendo necesarias; no se garantiza una latencia fija ni
ejecución instantánea. Instalar esta mejora requiere un reinicio manual para
cargarla en un proceso que ya esté abierto.

El colector sigue registrando todos los lanzamientos para calcular el historial,
pero reserva las consultas y la preparación para candidatos con la compra
inicial del dev verificada. Con historial positivo en caché, ese evento puede
iniciar la preparación inmediatamente, sin esperar volumen ni otra wallet.
Las colas priorizan creaciones recientes aptas. El perfil de historial mantiene
una sola consulta pendiente, sin un segundo dev esperando detrás del semáforo;
las dos lecturas de preparación de curvas siguen limitadas y en paralelo.
El historial desconocido o inferior al 1% no ocupa esos preparadores.

Los datos de entrada conservan `creation_timestamp`, `source_seen_at`,
`token_prepared_at`, `signal_ready_at` y `signal_age_seconds`. Permiten separar
el retraso del stream, la preparación y el envío. Estas mejoras de cola no
garantizan inclusión inmediata; el selector de segundo bloque descarta las
preparaciones tardías.

El presupuesto de nuevas entradas del dev es el menor entre el 80% del SOL
líquido confirmado y **30 USD**, incluidos margen y gastos estimados.
`max_entry_budget_usd: 30.0` se convierte a SOL con una referencia reciente;
si falta esa referencia, no se admite la compra. Mantiene la reserva de 0,03 SOL, nueve
plazas, deduplicación y tandas de cinco por ROI neto. El SL sigue al 22%; al
+35% recupera el equivalente nominal a la inversión más 12% y reparte el resto
en tres ventas, con objetivos +89%, +164,6% y +270,44% desde la entrada.

La validación de arranque `--only-dev` usa también el 80% y el tope inclusivo
de 30 USD. El tope se vuelve a comprobar antes de enviar: si la referencia
SOL/USD aumenta y el presupuesto ya lo supera, se descarta ese intento.
Las posiciones ya abiertas conservan sus importes registrados.

Las nueve plazas son el techo compartido, incluidas reservas, envíos inciertos
y remanentes. La evaluación independiente sigue siendo de cinco operaciones:
en modo solo dev, una tanda admite hasta cinco y espera sus cierres antes de
continuar. El techo de nueve no elimina esa regla ni garantiza ocupar nueve
plazas con una sola estrategia. Una tanda con ROI neto no positivo conserva
su pausa; subir estos límites no la reinicia.

La prioridad de compra tiene un tope de **0,07 USD por intento** y todas las
salidas del dev, incluidos TP, SL y ruta Jupiter, **0,03 USD por intento**. Son
límites del componente de prioridad: renta, comisión base y del protocolo van
aparte. Una referencia SOL/USD no válida impide comprar; las salidas siguen
permitidas con prioridad cero si la referencia está caducada.

Se mantienen la identidad on-chain del dev y de la curva, las verificaciones
de formato y disponibilidad, la elegibilidad vigente antes de firmar y la
protección de saldos y envíos inciertos. El perfil estricto descarta candidatos
al terminar el segundo bloque y conserva la deduplicación durante diez minutos.

El parámetro clásico de MC permanece en el archivo para compatibilidad, pero
no se usa en `creator_migration_only`. Activar `require_first_block_activity`
añade los mínimos opcionales de dos compradores y 250 USD. El selector actual
los omite, pero mantiene la prueba del dev y el segundo bloque. Las otras cuatro estrategias mantienen
sus filtros e historial.

En el perfil clásico `entry_rule: second_block`, el mecanismo
`third_block_retry_on_sharp_rise: true` solo actúa si el filtro
de subida brusca está activo y produce ese rechazo antes de firmar o enviar en
el segundo bloque. Con el filtro quitado no se activa por ese motivo. Cuando
actúa, permite un único nuevo intento en el tercero observado. Se espera la
limpieza completa del primero y se repiten los filtros activos. Si el
rechazo llega todavía dentro de la ventana fresca del tercero, también puede
intentarse; fuera de esa ventana se descarta. No hay intento en el cuarto,
ni reintento tras un envío firmado, incierto o con recibo.

Arranque **manual con fondos reales**, desde la consola del droplet:

```bash
cd /root/ai-bot-four-strategies
bash reiniciar_four_strategies.sh --real --only-dev
tail -n 40 logs/four-strategies.log
```

El script valida primero la selección y los servicios. Si hay un bloqueo,
termina antes de detener los monitores existentes. Si pasa, conserva la
conciliación de ventas manuales y espera a que termine la instancia anterior
antes de solicitar el nuevo proceso. El comando no crea otra instancia paralela.
La solicitud de arranque requiere comprobar después el proceso y los registros.

La estrategia dev debe tener `enabled: true` en
`configs/second-block-dev.yaml`. La configuración del repositorio permanece
desactivada por defecto; el archivo del droplet conserva su activación previa.
No se deben borrar las otras políticas del YAML: forman parte de la identidad
histórica del libro. El cartel del nuevo proceso identifica `SOLO DEV`.
Los cambios del tope requieren reiniciar manualmente para cargar la nueva
configuración en el proceso.

Para diagnóstico sin arrancar:

```bash
.venv/bin/python run_four_strategies.py --only-dev
```

La selección se especifica en cada arranque. Omitir `--only-dev` vuelve al modo
de todas las fuentes habilitadas, conservando sus pausas existentes.
