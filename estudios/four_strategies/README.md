# Revisión de cuatro estrategias — 2 de noviembre de 2026

Estado: código integrado localmente y probado con RPC/compradores simulados.
No se ha activado ni desplegado en ningún servidor. La configuración opt-in es
`configs/four-strategies.yaml`, fuera de `bots/` y con `enabled: false`.
`request.json` conserva el pedido y las interpretaciones.

| Estrategia | Entrada propuesta | Pendiente |
| --- | --- | --- |
| Presión y volumen | MC 8K–21K; 12 compradores, ratio 2,0, compras 2,5 SOL/8s, volumen total 1.300 USD | Aflojado el 3 de noviembre por petición del usuario. Ritmo 60–120s no fuerza compras. |
| Telegram inmediata | Nuevos mensajes de Wagbocooks, LegendsAlphas, calledbyblakee, dextools_nitro, cryptolistings | Descarta mensajes de más de 30s y compras migradas. Sin sesión autorizada, las otras fuentes siguen. |
| Primer bloque | Cuatro compradores distintos en el slot de creación observado, volumen comprado 450 USD | Ventana máxima 8s; volumen tomado de la copia histórica del primer bloque. |
| Fuerza 21K | Cruce ascendente observado de 21K, 13 compradores, ratio 2,2, compras 3 SOL/8s, volumen 1.800 USD | Observación inferior a 21K durante los últimos 8s. |

Los umbrales de presión/volumen son propuestas tomadas de copias históricas
locales, no resultados de optimización ni valores confirmados por este pedido.

## Escalera de salida preparada

SL inicial solicitado: −25%. Primer TP solicitado: +50%. Se propone vender
50% de la cantidad original, luego 25% y 25%. Se interpreta «haz eso» como
mantener el primer TP +50% y subir los otros dos objetivos si no caben antes
de 28K. Los porcentajes y el cálculo intermedio son decisiones del borrador.

El último objetivo sigue en 28K si está por encima del primer TP. Si no,
sube a entrada ×2; el segundo objetivo es el punto medio entre el primero
y el último. La misma regla se aplica a cualquiera de las cuatro estrategias,
incluidas las entradas variables de Telegram. El SL inicial sigue en −25%.

| Entrada MC | TP1, vende 50% original | TP2, vende 25% original | TP3, vende 25% original | SL inicial |
| --- | --- | --- | --- | --- |
| 8.000 USD | 12.000 USD | 20.000 USD | 28.000 USD | 6.000 USD |
| 21.000 USD | 31.500 USD | 36.750 USD | 42.000 USD | 15.750 USD |

Estos son umbrales de la revisión, no precios de ejecución garantizados.
Vender 50% de tokens a +50% recupera 75% del importe inicial antes de costes;
no recupera todo el capital. El cálculo tampoco descuenta comisiones o impacto.

`src/trading/review_exit_ladder.py` calcula los niveles y el monitor nuevo los
usa por posición, con el precio de entrada y SOL/USD guardados en la compra.
Los TP completados y los parciales pendientes sobreviven a un reinicio.
La salida protectora por volumen vendedor está desactivada por petición del
usuario (`anti_rug_ratio: 0`). No inicia ventas por detectar 1 SOL vendido en una
ventana temporal. El SL de −25% y los TP siguen activos en el código; el SL no se
cambia automáticamente a entrada.

## Coordinador y límites

### Revisión anti pump y dump — 3 de noviembre de 2026

La petición de conservar medidas anti pump y dump queda registrada en
`request.json`. La salida por detectar ventas de 1 SOL sigue desactivada.

El paquete exige señales recientes, compradores distintos para las fuentes
on-chain y nueva comprobación de la curva, los límites de MC correspondientes
y la señal antes de firmar. Conserva SL de −25%, presupuesto por entrada del 20%,
tres posiciones compartidas y pausa de cada estrategia tras tres pérdidas en
sus últimos cinco cierres verificados. El SL es un disparador de venta y no
garantiza una pérdida máxima del 25%.

Estos controles no equivalen a detectar manipulación. Contar wallets distintas
no demuestra que sean independientes. No se ha integrado en este paquete un
filtro de concentración, wallets relacionadas o wash trading. La confirmación
de alcance para añadir filtros de subidas bruscas y concentración está pendiente.
Presión mantiene su techo de 21K, pero Telegram, primer bloque y el cruce de 21K
no tienen un techo global de MC. No se anuncia protección completa contra rugs.

`src/trading/four_strategy_book.py` integra la pausa por tres pérdidas entre
los últimos cinco cierres verificados de cada estrategia. Se evalúa al disponer
de cinco cierres. Cero no cuenta como pérdida, un recibo duplicado no cuenta
dos veces y un reinicio no levanta una pausa. Los monitores existentes siguen
activos cuando su estrategia queda pausada.

Las cuatro estrategias comparten admisión SQLite con un máximo de tres
posiciones, incluyendo envíos pendientes, runners y ventas parciales. No se
recompra una moneda cerrada ni se compra desde dos fuentes. Un único colector
alimenta el volumen para evitar contarlo varias veces. El límite no controla
bots externos que no utilicen este coordinador.

Cada presupuesto es el 20% del SOL líquido confirmado antes de esa entrada,
incluyendo margen de slippage y reserva estimada para gastos, y conservando
0,03 SOL. Se recalcula con el saldo nuevo, sin valorar los tokens abiertos.
La compra nominal es menor que ese 20% porque los gastos están incluidos.

La función de tope de prioridad convierte 0,08 USD a lamports con una cotización
SOL/USD de hasta 30 segundos y redondea hacia abajo el precio por CU. No es un
tope global de costes, slippage o reintentos. Está conectado a ventas protectoras
en la curva y al fallback Jupiter, comprobando el presupuesto de CU de la
transacción devuelta. Con precio caducado se permite salir sin prioridad
adicional. No garantiza ejecución en un rug. Referencia:
[Jupiter Swap API](https://developers.jup.ag/docs/api-reference/swap/v1/swap).

La cola existente `confirmed_callouts` ahora asigna frases sin repetir entre
estrategias y reinicios y conserva firma e identificación de estrategia.
Prepara textos tras compra confirmada. El publicador de navegador descrito abajo
consume esta cola; no se ha publicado ningún callout real durante la preparación.

El destino autorizado por el usuario el 3 de noviembre de 2026 es
[@AImemebot](https://pump.fun/profile/AImemebot). La sesión observada inicialmente
correspondía a HGUX…g3Uk; después el usuario cambió la wallet de operaciones a
43w6…pZHV. Antes de publicar, el publicador exige que la cuenta conectada también
corresponda a esta wallet actual; la autorización de la sesión sigue pendiente.
`four_strategies.callout_account` se copia a `publisher_account` en cada entrada
nueva de la cola. La [API oficial de Pump.fun](https://docs.pump.fun/) solo ofrece
lectura; `src/monitoring/callout_publisher.py` usa el formulario visible de la web.

`callout_publication.enabled: true` conecta el publicador al arranque de estas
cuatro estrategias, pero el bot sigue con `enabled: false`. Hace falta preparar
una sesión de Chrome propia del publicador una vez. No copia cookies del Chrome
personal ni utiliza la clave privada para iniciar sesión.

En Windows, abrir `configurar-callouts.bat`: abre el perfil dedicado, el usuario
inicia sesión manualmente como @AImemebot y pulsa Enter en la consola para
verificarla. Después arranca solo el publicador, sin arrancar compras. Para
arranques siguientes del publicador por separado:

```powershell
.\.venv\Scripts\python.exe run_callout_publisher.py
.\.venv\Scripts\python.exe run_callout_publisher.py --setup
.\.venv\Scripts\python.exe run_callout_publisher.py --run
```

El comando sin argumentos muestra el estado local sin abrir navegador ni leer
credenciales. La dependencia opcional `playwright` ya está instalada en esta
`.venv`; en otro equipo se instala con `pip install -r requirements-callout-publisher.txt`. Usa
Chrome instalado y un perfil separado en `.pumpfun-callout-profile/`, ignorado
por Git. El usuario realiza cualquier autenticación o instalación de su wallet.

El publicador comprueba la cuenta conectada, la dirección completa del perfil,
el recibo de compra exitoso firmado por la wallet, la posición en el ledger
compartido y el saldo actual del token en cadena. Repite la verificación después
de preparar el formulario. Solo procesa entradas de menos de 120 segundos y
respeta el requisito visible de Pump.fun de mantener al menos 1 USD del token;
no compra más tokens para cumplirlo. Consulta la cola cada 2 segundos.

Antes de pulsar publicar guarda `sending` en
`estudios/four_strategies/callout-publications.sqlite3`. Solo registra `published`
al encontrar el texto, autor y enlace del callout en la página de la moneda.
Una respuesta perdida queda en `sending` para revisión manual y no se reenvía
automáticamente tras reiniciar. El estado de publicación está en esa base,
separado del JSONL de eventos originales para no competir con sus escritores.

Las pruebas del publicador usan formularios y RPC simulados. Se ha inspeccionado
el formulario real, pero falta autenticar el perfil propio y verificar el primer
callout real con una compra elegible; la publicación real no está validada aún.

Cada cierre exige recibos finalizados y cero tokens en cadena. El ledger guarda
la firma antes de transmitir; una respuesta perdida no devuelve una plaza.
Un rechazo de preflight probado permite liberar una compra sin broadcast.
Si la compra aterrizó pero se perdió su respuesta, se recupera desde el recibo.
Los envíos de resultado desconocido permanecen bloqueados hasta tener evidencia.

`estudios/four_strategies/report.json` se actualiza al verificar un cierre:
P&L neto con comisiones, ganadoras/no ganadoras y medianas de rasgos de entrada
por grupo y estrategia. Los abiertos no son ganancias. Es análisis descriptivo;
no modifica filtros ni presenta muestras pequeñas como rentabilidad validada.
Los eventos están en `events.jsonl` junto al ledger.

## Verificación local

`run_four_strategies.py` sin argumentos valida sin cargar `.env`, credenciales,
sesiones ni conexiones. Ejecutar exige una opción explícita y habilitar la
configuración. La validación no arranca compras. El runtime exige la wallet
pública solicitada `43w6CJkHjZpAjPU3LswTGwur5RvngKoTKakhfLBkpZHV`.
Las credenciales de este paquete se leen de `.env.four-strategies`, ignorado por
Git. El `.env` original se conserva para evitar cambiar la identidad de otros
procesos. El publicador usa el mismo `env_file` indicado por el YAML.

El diagnóstico `--preflight` comprueba en local el entorno y la dirección pública
que deriva de la clave, sin mostrarla. Con `--online` consulta RPC/mainnet, saldo,
websocket, cotización Jupiter, fuente SOL/USD y acceso a los canales con la sesión
existente. No firma ni construye swaps, no compra/vende ni envía mensajes.

En Windows, desde la raíz del repositorio:

```powershell
.\.venv\Scripts\python.exe run_four_strategies.py
.\.venv\Scripts\python.exe run_four_strategies.py --preflight
.\.venv\Scripts\python.exe run_four_strategies.py --preflight --online --report estudios/four_strategies/readiness-online.json
```

El arranque manual exige pasar de nuevo el diagnóstico online. Una wallet distinta
o un saldo que no permita conservar la reserva bloquean el inicio. Los informes y
el estado de ejecución están ignorados por Git. No contienen claves ni direcciones
de RPC privadas, pero sí pueden contener saldo y dirección pública de la wallet.

Se ha actualizado Jupiter a `https://api.jup.ag` con soporte opcional de
`JUPITER_API_KEY` en `.env.four-strategies`. Sin clave se espacian las peticiones a 0,5 por segundo,
según la [documentación oficial](https://developers.jup.ag/docs/portal/setup).
Esa limitación puede ralentizar las cotizaciones de posiciones migradas.

El monitor puede ejecutar una salida usando recibos y saldo confirmados para no
esperar a la finalización al proteger una posición. La auditoría contable y la
liberación de su plaza siguen exigiendo recibos finalizados y saldo cero.

No se han enviado transacciones reales. Las pruebas de ejecución usan
compradores/RPC simulados; el diagnóstico online solo hace lecturas. Se conservan
las configuraciones existentes; este paquete no las detiene ni despliega. El punto
de entrada de estas cuatro estrategias es `run_four_strategies.py`; el arranque
genérico del proyecto sigue pudiendo leer otros YAML existentes.
