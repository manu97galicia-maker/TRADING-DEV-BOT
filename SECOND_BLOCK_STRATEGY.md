# Estrategia dev: perfil de historial y perfil clásico

El perfil activo es ahora `entry_rule: creator_migration_only`: exige un
dev firmante con al menos 1% de migraciones PumpSwap verificadas. Con el selector
actual `require_first_block_activity: true`, añade dos compradores distintos
y al menos 250 USD de volumen total en el bloque de creación. Compras y ventas
suman al volumen; las operaciones posteriores no completan ese requisito.
No aplica los límites clásicos de MC, concentración, variaciones del precio
ni una ventana fija de segundo/tercer bloque.
Compra cuando termina la verificación del historial y la preparación de una
curva compatible. Prioridad máxima: compra 0,07 USD, salidas 0,03 USD por intento;
comisiones base, del protocolo y renta aparte. Mantiene TP/SL y controles del libro.

El funcionamiento actual se detalla en `DEV_ONLY.md`. Las secciones siguientes
documentan el perfil clásico disponible con `entry_rule: second_block`.

El presupuesto activo de nuevas operaciones del dev es el menor entre el 80%
inclusivo del SOL líquido y **20 USD**, incluyendo margen y gastos estimados,
manteniendo 0,03 SOL de reserva. El tope `max_entry_budget_usd: 20.0` se
convierte a SOL con referencia reciente y se revisa antes de enviar; sin una
referencia válida, no se compra. Las cuatro estrategias originales y
el perfil clásico conservan su configuración histórica del 90%.
La configuración se carga al reiniciar manualmente; instalar estos archivos
no reinicia un proceso que ya está abierto.

La configuración actual `reject_sharp_price_rise: false` quita a la estrategia
dev el límite de subida del 35% frente al mínimo de los últimos dos segundos.
Mantiene el rechazo por caída superior al 10%, incremento de cotización superior
al 10%, falta de precios recientes, concentración, MC, historial y saldo.
Las otras estrategias mantienen sus límites de precio. Al no producirse un
rechazo `sharp_price_rise` del dev, el reintento del tercero por ese motivo
queda inactivo; no se amplía a otros motivos de rechazo.

Si se reactiva el filtro, `third_block_retry_on_sharp_rise: true` permite un único intento
adicional en el tercer bloque observado cuando el segundo fue rechazado
exactamente por `sharp_price_rise` antes de firmar. Ambos bloques se identifican
por su cadena de padres; no se suman números de slot. Un rechazo recibido ya
en el tercero solo sirve mientras sigan vigentes su reloj y su precio.

Cada intento tiene su propia copia de metadatos, target y nueva reserva. El
tercero espera que el primer manejador libere las reservas y la coordinación
por mint. Se vuelven a comprobar MC <4.200, historial del dev >=1%, volumen y
compradores del primer bloque, concentración, precio, saldo y comisiones. Los
trades posteriores solo actualizan el precio: no aumentan los compradores ni
el volumen exigido al primer bloque. Si el precio sigue subiendo bruscamente,
se vuelve a rechazar. Un recibo, firma, envío incierto, desconexión, cuarto
bloque o ventana caducada impide reutilizar ese intento. El nonce y el target
del primer intento nunca se transforman en una compra del tercero.

Configuración: `configs/second-block-dev.yaml`. La referencia está fuera de
`four_strategies` para conservar la identidad del libro de posiciones y sus
pausas. La estrategia nueva se llama `dev-second-block` y permanece
**desactivada en la configuración**, con activación manual. El proveedor
Helius ya se ha verificado con lecturas reales. La primera venta recupera el
capital invertido más un 12% de beneficio nominal.

Condiciones de entrada:

- Evento real de creación de Pump.fun observado, con curva PDA válida y oferta
  de 1.000 millones de tokens. El dev es el `user` firmante de la creación:
  debe ser una wallet con clave pública sobre la curva, no una PDA. El campo
  `creator` puede ser un receptor de comisiones distinto y se conserva para
  verificar la curva y construir las cuentas de la operación.
- Al menos dos wallets compradoras distintas en el bloque de creación.
- Al menos 275 USD negociados en ese bloque: compras más ventas. Compras de
  bloques posteriores no completan los requisitos del primero.
- Dev con al menos 1% de migraciones PumpSwap canónicas verificadas respecto
  a todos sus lanzamientos originales demostrados; el candidato queda fuera.
- Capitalización inferior a 4.200 USD al comprobar la curva. El `max_sol_cost`
  de la orden limita además el coste medio de compra, usando la misma
  referencia SOL/USD y 1.000 millones de oferta. No limita la capitalización
  posterior causada por la propia compra. Un cambio de SOL/USD posterior
  tampoco queda cubierto por ese techo en SOL.
- Ventana del siguiente slot producido cuyo padre es el slot de creación,
  hasta 0,6 segundos desde que se recibe la notificación. Hay nueva revisión
  antes de firmar y antes de enviar. Sin reintentos de envío ni del RPC.

El presupuesto es el **90% del SOL líquido confirmado**, incluidos margen de
slippage y gastos. La prioridad tiene tope de **0,06 USD por intento** con
cotización SOL/USD reciente; renta, comisión base y comisiones de protocolo
son conceptos distintos. Las otras cuatro estrategias también usan el 90% por
nueva operación, siempre respetando la reserva de 0,03 SOL y los gastos pendientes.
Las cinco comparten cuatro plazas y se evalúan por la suma neta de cada tanda
de cinco cierres, sin pausa por el número de pérdidas. Permanecen los filtros
de precio y concentración, el stop y la salida por inactividad configurada;
la salida por ventas de 1 SOL continúa desactivada. Además, cada estrategia usa
tandas independientes de cinco operaciones auditadas; el siguiente grupo solo
se admite con ROI neto positivo. Véase `FIVE_TRADE_EVALUATION.md`.
En la quinta se consulta también la tenencia del firmante real, incluso fuera
del top 20, y se le aplica el límite de concentración del 5%, además del mismo
límite para el receptor de comisiones.

Los objetivos multiplicativos son +35%, +89%, +164,6% y +270,44% respecto a
la entrada. Tras la primera venta, el resto se reparte en tres partes iguales.
La opción elegida es `first_sale_mode: recover_capital_plus_profit`, con
`recovery_profit_fraction: 0.12`. El stop solicitado es del 22%; el «12»
corresponde al beneficio recuperado. Al primer objetivo exacto
de +35%, se vende el 82,962963% de los tokens para recuperar el equivalente al
112% de la inversión. Cada venta posterior corresponde al 5,679012% original.
Si el primer precio observado supera +35%, la fracción de recuperación se
recalcula con ese precio; queda guardada antes de vender para evitar duplicados
tras un reinicio. Es una recuperación nominal: gastos, impacto y deslizamiento
pueden reducir lo cobrado.

El proveedor configurado es `history_provider: helius_transactions`. Se consulta
`getTransactionsForAddress` del firmante, con transacciones completas, paginación
hasta cursor nulo y un límite superior fijo de slot finalizado. Una página corta
o vacía con cursor no termina el historial. Solo cuentan `CreateEvent` auténticos
de ejecuciones correctas de Pump.fun y cuyo `user` firmó la transacción. Los
eventos de invocaciones revertidas se descartan; logs truncados, formatos
desconocidos, duplicados y paginación incompleta impiden validar el historial.

Se prueban las migraciones leyendo las curvas y los pools PumpSwap canónicos
con `getMultipleAccounts`. `complete: true` sin pool no acredita migración.
El receptor de comisiones puede haber cambiado sin borrar los lanzamientos
originales del denominador. Si ya hay suficientes migraciones demostradas, se
puede terminar su comprobación antes de recorrer todos los pools: el número de
lanzamientos permanece completo y el ratio es un mínimo demostrado. Raydium
antiguo y pools desaparecidos no suman a ese mínimo.

Las consultas son de fondo y están limitadas a 12 segundos, 64 páginas de 100
transacciones, hasta 10.000 transacciones y 10.000 lanzamientos almacenados por
consulta. La caché dura como máximo 600 segundos; las creaciones observadas se
añaden al denominador. Cualquier interrupción invalida la caché. Al conectar,
el ancla histórica debe alcanzar el primer slot observado para cubrir el hueco
entre el historial finalizado y el stream. Sin esa cobertura o sin datos
completos y recientes, se descarta la entrada.

Comprobación real del 3 de noviembre de 2026: la wallet firmante
`9qGSkpuGWGp7uqxuPw27ZJ2x1zSV8Svm8rmDWMmuG1W6` tuvo 287 transacciones en
cuatro páginas, un lanzamiento Pump.fun y una migración PumpSwap verificada.
El lector definitivo lo comprobó en 394 ms con seis lecturas RPC y ancla
464874793. GMGN devolvió también un lanzamiento y una graduación. En ese token,
`creator` era otra wallet: consultar su historial daba cero registros en GMGN.
GMGN se usa aquí como contraste de los datos; no reemplaza la prueba de pools
ni se toma la longitud de su lista limitada de tokens como historial completo.

No se garantiza ser la primera transacción ni quedar incluido en el segundo
bloque: el websocket informa de lo procesado por el nodo y el envío puede
llegar después. El auditor guarda `actual_buy_slot` y `target_slot_matched`
cuando obtiene confirmación final. Ningún filtro garantiza evitar rugs.

La validación sin credenciales es `python run_four_strategies.py`. No inicia
operaciones. Los cambios de código no reinician el proceso que ya está abierto.
Para repetir exclusivamente la comprobación del historial:

```bash
python inspect_creator_history.py --developer 9qGSkpuGWGp7uqxuPw27ZJ2x1zSV8Svm8rmDWMmuG1W6
```

El diagnóstico utiliza solo el RPC configurado y no importa ninguna wallet.

Referencias técnicas: [slotSubscribe de Solana](https://solana.com/docs/rpc/websocket/slotsubscribe),
[historial paginado de Helius](https://www.helius.dev/docs/api-reference/rpc/http/gettransactionsforaddress),
[consulta de creadores de GMGN](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-portfolio/SKILL.md),
[IDL de Pump](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json) y
[IDL de PumpSwap](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json).
