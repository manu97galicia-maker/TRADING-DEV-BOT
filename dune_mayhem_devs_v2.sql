-- =====================================================================
-- MAYHEM devs (pump.fun) — VERSION AMPLIADA (mas devs, buena mediana)
-- Cambios vs v1 para sacar BASTANTES MAS:
--   * ventana 30 dias (antes 14)
--   * min 2 lanzamientos mayhem (antes 3)
--   * mediana >= 20K (antes 25K)  <- sigue siendo "buena"
--   * LIMIT 300 (antes 100)
-- Ordena por mediana de MC (lo que pediste) y luego por % que pumpean.
-- =====================================================================

WITH mayhem_creates AS (
    SELECT
        c.mint,
        COALESCE(c.creator, c."user") AS dev,
        c.evt_block_time              AS created_at
    FROM pumpdotfun_solana.pump_evt_CreateEvent c
    WHERE c.evt_block_time >= NOW() - INTERVAL '30' day
      AND c.is_mayhem_mode = true
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

joined AS (
    SELECT m.dev, m.mint, COALESCE(p.peak_mc_usd, 0) AS peak_mc_usd
    FROM mayhem_creates m
    LEFT JOIN token_peak p ON p.mint = m.mint
)

SELECT
    dev                                                         AS wallet,
    COUNT(*)                                                    AS mayhem_launches,
    ROUND(APPROX_PERCENTILE(peak_mc_usd, 0.5))                  AS median_peak,
    ROUND(MAX(peak_mc_usd))                                     AS best_peak_usd,
    ROUND(100.0 * COUNT_IF(peak_mc_usd >= 30000)  / COUNT(*), 1) AS pct_pump_30k,
    ROUND(100.0 * COUNT_IF(peak_mc_usd >= 100000) / COUNT(*), 1) AS pct_pump_100k
FROM joined
GROUP BY dev
HAVING COUNT(*) >= 2                                    -- min 2 mayhem
   AND APPROX_PERCENTILE(peak_mc_usd, 0.5) >= 20000     -- mediana >= $20K
ORDER BY median_peak DESC, pct_pump_100k DESC
LIMIT 300;
