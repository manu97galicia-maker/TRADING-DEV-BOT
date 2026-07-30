-- =====================================================================
-- ¿Que tienen en comun en los primeros 20s las monedas que PASAN $8K?
-- Compara WINNERS (pico >=$8K) vs LOSERS (<$8K) por sus metricas de 0-20s.
-- Asi ves la FIRMA del ganador (que medir al entrar). Ventana: 7 dias.
-- SOL fijo a $75 (para precision usar prices.usd real).
-- =====================================================================

WITH creates AS (
    SELECT mint, evt_block_time AS created
    FROM pumpdotfun_solana.pump_evt_CreateEvent
    WHERE evt_block_time >= NOW() - INTERVAL '7' day
),

-- Metricas de los PRIMEROS 20 SEGUNDOS de cada moneda
first20 AS (
    SELECT
        c.mint,
        SUM(CAST(t.sol_amount AS double)) / 1e9                    AS vol20_sol,
        COUNT(*)                                                  AS trades20,
        COUNT(DISTINCT t."user")                                  AS buyers20,
        COUNT_IF(t.is_buy)                                        AS buys20,
        COUNT_IF(NOT t.is_buy)                                    AS sells20,
        MAX(
            (CAST(t.virtual_sol_reserves AS double) / 1e9)
            / NULLIF(CAST(t.virtual_token_reserves AS double) / 1e6, 0)
            * 1e9 * 75.0
        )                                                         AS mc_at_20s
    FROM creates c
    JOIN pumpdotfun_solana.pump_evt_TradeEvent t
      ON t.mint = c.mint
     AND t.evt_block_time BETWEEN c.created AND c.created + INTERVAL '20' second
    GROUP BY c.mint
),

-- Pico historico de cada moneda (para clasificar winner/loser)
peak AS (
    SELECT
        t.mint,
        MAX(
            (CAST(t.virtual_sol_reserves AS double) / 1e9)
            / NULLIF(CAST(t.virtual_token_reserves AS double) / 1e6, 0)
            * 1e9 * 75.0
        ) AS peak_mc
    FROM pumpdotfun_solana.pump_evt_TradeEvent t
    GROUP BY t.mint
),

joined AS (
    SELECT
        f.*,
        CASE WHEN p.peak_mc >= 8000 THEN 'WINNER (paso 8K)' ELSE 'loser (<8K)' END AS grupo
    FROM first20 f
    JOIN peak p ON p.mint = f.mint
)

SELECT
    grupo,
    COUNT(*)                                                     AS n_monedas,
    ROUND(APPROX_PERCENTILE(vol20_sol, 0.5), 1)                  AS vol20_sol_mediana,
    ROUND(APPROX_PERCENTILE(buyers20, 0.5))                      AS buyers20_mediana,
    ROUND(APPROX_PERCENTILE(trades20, 0.5))                      AS trades20_mediana,
    ROUND(AVG(CASE WHEN trades20 > 0 THEN 100.0 * buys20 / trades20 END), 0) AS pct_buys_20s,
    ROUND(APPROX_PERCENTILE(mc_at_20s, 0.5))                     AS mc_a_20s_mediana,
    ROUND(APPROX_PERCENTILE(vol20_sol, 0.25), 1)                 AS vol20_p25,
    ROUND(APPROX_PERCENTILE(buyers20, 0.25))                     AS buyers20_p25
FROM joined
GROUP BY grupo
ORDER BY grupo DESC;
