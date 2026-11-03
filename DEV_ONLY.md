# Entradas únicamente de dev con migraciones verificadas

`--only-dev` admite nuevas entradas solo de `dev-second-block`. Presión
compradora, cruce de 21k, primer bloque de cuatro compradores y señales de
Telegram no abren posiciones en este modo. El colector de Solana permanece
conectado para observar creación, primer bloque, historial del dev y segundo
bloque; no se abre la sesión de señales de Telegram.

Se conservan el mismo libro, bloqueo de proceso, posiciones, tandas y pausas.
Las posiciones antiguas de cualquier estrategia mantienen sus monitores de
salida y auditoría. La selección no borra pérdidas ni reinicia la evaluación.

Los parámetros siguen siendo: dev firmante con al menos 1% de migraciones
PumpSwap verificadas, dos compradores distintos y 275 USD de volumen en el
primer bloque, MC inferior a 4.200 USD, presupuesto inclusivo del 90% del SOL,
reserva de 0,03 SOL, SL del 22% y tandas de cinco por ROI neto. La entrada se
intenta en el siguiente bloque observado; no se garantiza su inclusión.

Con `third_block_retry_on_sharp_rise: true`, un rechazo exacto por subida
brusca, antes de firmar o enviar en el segundo bloque, permite un único nuevo
intento en el tercero observado. Se espera la limpieza completa del primer
intento y se repiten todos los filtros, incluido el de subida brusca. Si el
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

Para diagnóstico sin arrancar:

```bash
.venv/bin/python run_four_strategies.py --only-dev
```

La selección se especifica en cada arranque. Omitir `--only-dev` vuelve al modo
de todas las fuentes habilitadas, conservando sus pausas existentes.
