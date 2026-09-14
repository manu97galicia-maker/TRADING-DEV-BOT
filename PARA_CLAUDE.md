# Nota para Claude

## Estado actual

La estrategia permanece exclusivamente en paper. La configuración exige
`paper_trade: true` y `max_real_buys: 0`; no deben cambiarse durante sesiones
de desarrollo.

## Detección activa

- DexScreener paid/boost mediante API, con sondeo cada segundo.
- El evento paid debe tener como máximo 10 minutos.
- No hay límite de edad del token si conserva volumen en 24 horas.
- Los tokens aún en pump.fun y los migrados a PumpSwap se procesan por rutas
  paper separadas.
- La fotografía inicial se utiliza como línea base y no genera entradas sobre
  señales históricas.

DEXTools está disponible como cliente de datos, pero Nitro no está conectado
como fuente directa. MoonTok Ads/Listings y DEXTools Nitro requieren una API o
webhook compatible, o bien una sesión Telegram autorizada. La lectura Telegram
está desactivada. Discord, Birdeye y GMGN también permanecen desactivados.

## Entradas simuladas

- Paid normal en pump.fun: 0,30 SOL.
- Boost de 1 a 99: 0,35 SOL.
- Boost igual o superior a 100: 0,40 SOL.
- PumpSwap migrada con paid/boost posterior: 0,35 SOL.
- Sin máximo de posiciones configurado.
- Reserva mínima configurada: 0,03 SOL. En paper no se consume la wallet.

## Salidas

- +25%: vender 30%.
- +50%: vender 25%.
- +75%: vender 25%.
- x2, x3, x5, x10 y x20: vender 4% en cada escalón.
- Stop-loss inicial: -18%.
- Sin máximo de holdeo; una posición termina por TP o SL.

## Latencia

El sondeo de un segundo no garantiza ser el primer comprador. DexScreener puede
publicar un pago o boost minutos después del evento. También existen latencias
de mercado, RPC y construcción/confirmación de transacciones.

## Avisos y seguridad

Los avisos salientes de Telegram pueden seguir activos mediante el bot. No
guardar claves, tokens, sesiones ni estadísticas locales en Git. Antes de real:

1. Rotar cualquier clave que haya aparecido en una captura.
2. Acumular una muestra paper suficiente por fuente.
3. Validar compra y venta de tokens existentes y PumpSwap con importes mínimos.
4. Verificar TP/SL, parada de emergencia y comportamiento bajo concurrencia.
5. Confirmar que no existen posiciones abiertas antes de reiniciar servicios.

Los detalles verificables están resumidos también en `LISTO.md`.
