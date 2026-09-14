# Estado de la estrategia paid/boost

## Operativo

- Ejecución protegida en modo paper (`paper_trade: true`).
- Compras reales bloqueadas (`max_real_buys: 0`).
- Sondeo de DexScreener paid/boost cada segundo.
- Sin límite de edad del token cuando conserva volumen en 24 horas.
- Paid normal: 0,30 SOL simulados.
- Boost de 1 a 99: 0,35 SOL simulados.
- Boost de 100 o más: 0,40 SOL simulados.
- Tokens migrados a PumpSwap con paid/boost posterior: 0,35 SOL simulados.
- Sin máximo de posiciones configurado y con reserva mínima de 0,03 SOL.
- Sin cierre por tiempo: las posiciones terminan mediante TP o SL.

## Salidas

- +25%: vender 30%.
- +50%: vender 25%.
- +75%: vender 25%.
- x2, x3, x5, x10 y x20: vender 4% en cada escalón.
- Stop-loss inicial: -18%.
- Tras alcanzar TP1, el stop sube como indica la estrategia configurada.

## Latencia y alcance

El proceso consulta la API cada segundo y actúa ante una señal nueva, pero no se
puede garantizar ser el primer comprador. DexScreener puede publicar un pago o
boost en su API después de que haya ocurrido. La latencia real incluye la
publicación del proveedor, la consulta de mercado, el RPC y, en modo real, la
construcción y confirmación de la transacción.

DEXTools Nitro y MoonTok Ads/Listings no están conectados como fuentes de
entrada. Requieren un endpoint compatible o una sesión de Telegram autorizada.
Discord, Birdeye y GMGN permanecen desactivados.

## Antes de operar con fondos

La ruta de compra de tokens existentes y la ruta de PumpSwap deben probarse con
cantidades mínimas siguiendo `AGENTS.md`. También debe rotarse cualquier clave
privada o token que haya aparecido en una captura. Este documento no autoriza ni
activa operaciones reales.
