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
`require_first_block_activity: true`. Exige que el dev firmante tenga al menos
un 1% de sus lanzamientos anteriores migrados a PumpSwap y verificados mediante
Helius. Además, el bloque de creación debe registrar **dos compradores
distintos y al menos 250 USD de volumen total**, sumando compras y ventas.
La misma dirección cuenta una sola vez como comprador; los eventos de bloques
posteriores no aumentan esos contadores.

No exige MC, concentración, historial de variaciones de precio ni entrar en
un bloque dos/tres concreto. Observa nuevas creaciones y espera a verificar su historial
y a preparar una curva comprable; no compra monedas ya migradas a un pool.

La preparación consulta tanto la curva como la cuenta del token con
`commitment: processed`, en paralelo, para no esperar la finalización de una
creación recién observada. El colector del perfil de historial no consulta
SQLite por cada candidato y evento: las pausas y el ROI se verifican al entrar
en el manejador y de nuevo en la reserva atómica antes de enviar.
La verificación del historial del dev, una conexión operativa y la inclusión
por la red siguen siendo necesarias; no se garantiza una latencia fija ni
ejecución instantánea. Instalar esta mejora requiere un reinicio manual para
cargarla en un proceso que ya esté abierto.

El presupuesto de nuevas entradas del dev es el menor entre el 80% del SOL
líquido confirmado y **20 USD**, incluidos margen y gastos estimados.
`max_entry_budget_usd: 20.0` se convierte a SOL con una referencia reciente;
si falta esa referencia, no se admite la compra. Mantiene la reserva de 0,03 SOL, cuatro
plazas, deduplicación y tandas de cinco por ROI neto. El SL sigue al 22%; al
+35% recupera el equivalente nominal a la inversión más 12% y reparte el resto
en tres ventas, con objetivos +89%, +164,6% y +270,44% desde la entrada.

La validación de arranque `--only-dev` usa también el 80% y el tope inclusivo
de 20 USD. El tope se vuelve a comprobar antes de enviar: si la referencia
SOL/USD aumenta y el presupuesto ya lo supera, se descarta ese intento.
Las posiciones ya abiertas conservan sus importes registrados.

La prioridad de compra tiene un tope de **0,07 USD por intento** y todas las
salidas del dev, incluidos TP, SL y ruta Jupiter, **0,03 USD por intento**. Son
límites del componente de prioridad: renta, comisión base y del protocolo van
aparte. Una referencia SOL/USD no válida impide comprar; las salidas siguen
permitidas con prioridad cero si la referencia está caducada.

Se mantienen la identidad on-chain del dev y de la curva, las verificaciones
de formato y disponibilidad, la elegibilidad vigente antes de firmar y la
protección de saldos y envíos inciertos. Los límites de memoria conservan
candidatos durante diez minutos y evitan repetir un mint en esa ventana.

El parámetro clásico de MC permanece en el archivo para compatibilidad, pero
no se usa en `creator_migration_only`. La actividad del primer bloque sí se
exige con el selector actual; omitir o desactivar ese selector conserva la
variante opcional de solo historial. Las otras cuatro estrategias mantienen
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
