-- =====================================================================
-- PUMP.FUN devs (TODOS los lanzamientos, no solo mayhem) — con filtro de
-- CALIDAD real: anti-granja + baja varianza. Sirve para re-curar ELITE/NEXT-BIG.
--
-- Mide sobre TODOS los lanzamientos del dev (30d), no cherry-picking:
--   total_launches  -> descarta granjas (max ~2/dia)
--   pct_migrated    -> % de sus tokens que completan la curva (~$65K)
--   median_peak(P50)-> el caso tipico
--   p25_peak (P25)  -> el SUELO = consistencia / baja varianza  <<< la clave
--   hit_rate_20k    -> % que llegan a $20K
-- =====================================================================

WITH all_creates AS (
    SELECT
        COALESCE(c.creator, c."user") AS dev,
        c.mint
    FROM pumpdotfun_solana.pump_evt_CreateEvent c
    WHERE c.evt_block_time >= NOW() - INTERVAL '30' day
),

token_peak AS (
    SELECT
        t.mint,
        MAX(
            (CAST(t.virtual_sol_reserves AS double) / 1e9)
            / NULLIF(CAST(t.virtual_token_reserves AS double) / 1e6, 0)
            * 1e9
            * 150.0
        ) AS peak_mc_usd
    FROM pumpdotfun_solana.pump_evt_TradeEvent t
    GROUP BY t.mint
),

dev_stats AS (
    SELECT
        a.dev,
        COUNT(*)                                                    AS total_launches,
        APPROX_PERCENTILE(COALESCE(p.peak_mc_usd,0), 0.5)           AS median_peak,
        APPROX_PERCENTILE(COALESCE(p.peak_mc_usd,0), 0.25)          AS p25_peak,
        APPROX_PERCENTILE(COALESCE(p.peak_mc_usd,0), 0.10)          AS p10_peak,
        MAX(COALESCE(p.peak_mc_usd,0))                              AS best_peak,
        100.0 * COUNT_IF(COALESCE(p.peak_mc_usd,0) >= 20000)  / COUNT(*) AS hit_20k,
        100.0 * COUNT_IF(COALESCE(p.peak_mc_usd,0) >= 65000)  / COUNT(*) AS pct_migrated,
        100.0 * COUNT_IF(COALESCE(p.peak_mc_usd,0) >= 100000) / COUNT(*) AS pct_100k
    FROM all_creates a
    LEFT JOIN token_peak p ON p.mint = a.mint
    GROUP BY a.dev
)

SELECT
    dev                       AS wallet,
    total_launches,
    ROUND(pct_migrated, 1)    AS pct_migrated,
    ROUND(median_peak)        AS median_peak,
    ROUND(p25_peak)           AS p25_peak,       -- suelo / baja varianza
    ROUND(p10_peak)           AS p10_peak,
    ROUND(hit_20k, 1)         AS hit_rate_20k,
    ROUND(best_peak)          AS best_peak_usd,
    ROUND(pct_100k, 1)        AS pct_pump_100k
FROM dev_stats
WHERE total_launches BETWEEN 3 AND 60      -- min 3 (muestra real), max 60 (NO granja)
  AND pct_migrated >= 5.0                   -- al menos 5% completan la curva
  AND median_peak  >= 25000                 -- mediana >= $25K
  AND p25_peak     >= 18000                 -- BAJA VARIANZA: 75% llegan a ~$18K+
ORDER BY p25_peak DESC, pct_100k DESC
LIMIT 500;
