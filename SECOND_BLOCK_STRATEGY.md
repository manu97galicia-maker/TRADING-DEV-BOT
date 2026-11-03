# Estrategia adicional: dev y segundo bloque

Configuración: `configs/second-block-dev.yaml`. La referencia está fuera de
`four_strategies` para conservar la identidad del libro de posiciones y sus
pausas. La estrategia nueva se llama `dev-second-block` y queda **desactivada**
mientras no se aclare qué significa «12» en la primera venta/stop.

Condiciones de entrada:

- Evento real de creación de Pump.fun observado, con curva PDA válida, oferta
  de 1.000 millones de tokens y creador igual a la wallet que creó el token.
- Al menos dos wallets compradoras distintas en el bloque de creación.
- Al menos 275 USD negociados en ese bloque: compras más ventas. Compras de
  bloques posteriores no completan los requisitos del primero.
- Dev con al menos 1% de migraciones PumpSwap canónicas verificadas sobre sus
  curvas de Pump.fun; el token candidato queda fuera del cálculo.
- Capitalización inferior a 4.200 USD al comprobar la curva. El `max_sol_cost`
  de la orden limita además el coste medio de compra, usando la misma
  referencia SOL/USD y 1.000 millones de oferta. No limita la capitalización
  posterior causada por la propia compra. Un cambio de SOL/USD posterior
  tampoco queda cubierto por ese techo en SOL.
- Ventana del siguiente slot producido cuyo padre es el slot de creación,
  hasta 0,6 segundos desde que se recibe la notificación. Hay nueva revisión
  antes de firmar y antes de enviar. Sin reintentos de envío ni del RPC.

El presupuesto es el **23% del SOL líquido confirmado**, incluidos margen de
slippage y gastos. La prioridad tiene tope de **0,06 USD por intento** con
cotización SOL/USD reciente; renta, comisión base y comisiones de protocolo
son conceptos distintos. Las otras cuatro estrategias mantienen su 40%.
Las cinco comparten tres plazas y la pausa persistente por tres pérdidas en
los últimos cinco cierres completos de cada estrategia. Permanecen los filtros
de precio y concentración, el stop y la salida por inactividad configurada;
la salida por ventas de 1 SOL continúa desactivada.

Los objetivos multiplicativos son +35%, +89%, +164,6% y +270,44% respecto a
la entrada. Tras la primera venta, el resto se reparte en tres partes iguales.
El código admite explícitamente recuperar capital, recuperar capital más un
beneficio definido o vender una fracción original. **No se ha elegido ninguna**:
`first_sale_mode: pending` y `stop_loss_fraction: null` impiden activar esta
configuración. Recuperar capital se calcula con el precio observado al primer
disparo; la asignación queda guardada antes de vender para evitar duplicados
tras un reinicio. Es una recuperación nominal: gastos, impacto y deslizamiento
pueden reducir lo cobrado.

El historial se consulta fuera del camino de compra, con paginación completa y
límites de tiempo/memoria. Una página vacía con cursor no significa historial
completo. Se vuelve a consultar el denominador después de los pools; las
creaciones observadas se incorporan al denominador y cualquier interrupción
del stream invalida la caché. Sin historial completo y reciente se descarta la
entrada. Las migraciones antiguas a Raydium no cuentan en el numerador; las
reasignaciones de creador o cuentas cerradas limitan lo que puede demostrarse
con el estado actual. No es una garantía del historial de toda la vida del dev.
En la prueba de lectura del proveedor aparecieron páginas vacías con cursor;
**todavía no hay una comprobación real completa que valide un dev para el 1%**.

No se garantiza ser la primera transacción ni quedar incluido en el segundo
bloque: el websocket informa de lo procesado por el nodo y el envío puede
llegar después. El auditor guarda `actual_buy_slot` y `target_slot_matched`
cuando obtiene confirmación final. Ningún filtro garantiza evitar rugs.

La validación sin credenciales es `python run_four_strategies.py`. No inicia
operaciones. Los cambios de código no reinician el proceso que ya está abierto.

Referencias técnicas: [slotSubscribe de Solana](https://solana.com/docs/rpc/websocket/slotsubscribe),
[paginación de Helius](https://www.helius.dev/docs/api-reference/rpc/http/getprogramaccountsv2),
[IDL de Pump](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json) y
[IDL de PumpSwap](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json).
