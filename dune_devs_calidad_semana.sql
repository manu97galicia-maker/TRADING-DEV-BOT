-- =====================================================================
-- DEVS DE CALIDAD — ULTIMA SEMANA (7 dias)
-- Metricas: mediana de pico, DURACION VIVA del token, holders reales,
-- migracion, hit-rate, P25 (baja varianza). Anti-granja.
-- (El "pago DexScreener" se cruza DESPUES con la API de DexScreener - no esta en Dune)
--
-- NOTA: SOL hardcodeado a 150. Para precision, sustituir por prices.usd real (~$74).
-- =====================================================================

WITH all_creates AS (
    SELECT
        COALESCE(c.creator, c."user") AS dev,
        c.mint,
        c.evt_block_time AS created
    FROM pumpdotfun_solana.pump_evt_CreateEvent c
    WHERE c.evt_block_time >= NOW() - INTERVAL '7' day
),

token_stats AS (
    SELECT
        t.mint,
        MAX(
            (CAST(t.virtual_sol_reserves AS double) / 1e9)
            / NULLIF(CAST(t.virtual_token_reserves AS double) / 1e6, 0)
            * 1e9 * 150.0
        )                                                   AS peak_mc,
        -- DURACION VIVA: minutos entre el primer y ultimo trade del token
        date_diff('minute', MIN(t.evt_block_time), MAX(t.evt_block_time)) AS duration_min,
        COUNT(DISTINCT t."user")                            AS holders,   -- traders distintos = demanda real
        COUNT(*)                                            AS trades,
        SUM(CAST(t.sol_amount AS double)) / 1e9             AS vol_sol     -- volumen total en SOL
    FROM pumpdotfun_solana.pump_evt_TradeEvent t
    GROUP BY t.mint
),

joined AS (
    SELECT
        a.dev, a.mint,
        COALESCE(s.peak_mc, 0)      AS peak_mc,
        COALESCE(s.duration_min, 0) AS duration_min,
        COALESCE(s.holders, 0)      AS holders,
        COALESCE(s.vol_sol, 0)      AS vol_sol
    FROM all_creates a
    LEFT JOIN token_stats s ON s.mint = a.mint
)

SELECT
    dev                                                     AS wallet,
    COUNT(*)                                                AS launches_7d,
    ROUND(APPROX_PERCENTILE(peak_mc, 0.5))                  AS median_peak,
    ROUND(APPROX_PERCENTILE(peak_mc, 0.25))                 AS p25_peak,         -- suelo (baja varianza)
    ROUND(APPROX_PERCENTILE(duration_min, 0.5))             AS median_duracion_min,  -- << DURACION VIVA
    ROUND(APPROX_PERCENTILE(holders, 0.5))                  AS median_holders,       -- demanda real
    ROUND(APPROX_PERCENTILE(vol_sol, 0.5), 1)               AS median_vol_sol,
    ROUND(100.0 * COUNT_IF(peak_mc >= 65000) / COUNT(*), 1) AS pct_migrated,
    ROUND(100.0 * COUNT_IF(peak_mc >= 20000) / COUNT(*), 1) AS hit_rate_20k,
    ROUND(MAX(peak_mc))                                     AS best_peak
FROM joined
GROUP BY dev
HAVING COUNT(*) BETWEEN 3 AND 60                       -- anti-granja
   AND APPROX_PERCENTILE(peak_mc, 0.5)      >= 18000   -- mediana decente
   AND APPROX_PERCENTILE(peak_mc, 0.25)     >= 12000   -- baja varianza
   AND APPROX_PERCENTILE(duration_min, 0.5) >= 30      -- DURAN >= 30 min (no rug instantaneo)
   AND APPROX_PERCENTILE(holders, 0.5)      >= 15       -- demanda real (no bundle/bot)
ORDER BY median_duracion_min DESC, median_peak DESC
LIMIT 300;
