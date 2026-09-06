# Copy-trading de traders verificados

La fuente de PumpPortal sirve para seguir compras en vivo, no para consultar el
histórico. Por eso ninguna cartera entra directamente desde un ranking o una
lista antigua. Primero debe existir una exportación auditable de siete días.

## Reglas actuales

- Mediana de ROI de las calls cerradas en 7 días: **150 % o más**.
- Actividad: **5 calls o más** en 7 días.
- Porcentaje de calls ganadoras: **60 % o más**.
- Seguidores en X: máximo **15.000**; las cuentas con menos de **3.000** se
  ordenan primero.
- `source` es obligatorio en cada fila. Si falta una métrica, la cartera queda
  rechazada; el sistema nunca rellena datos inventados.

## Flujo

1. Copiar `trader_candidates.example.json` y añadir métricas verificadas.
2. Ejecutar `python calificar_traders.py candidatos.json`.
3. Revisar `copy_traders_verified.txt` y la fuente de cada aprobado.
4. Arrancar solo `bots/bot-copy-elite-paper.yaml` para medir entradas reales en
   simulación.
5. No pasar a real hasta revisar una semana completa y confirmar el stop.

La estrategia simula **0,1 SOL por entrada**, vende el **100 % al +80 %** y usa
provisionalmente un **stop del -24 %**. Está doblemente protegida con
`enabled: false` y `paper_trade: true`.
