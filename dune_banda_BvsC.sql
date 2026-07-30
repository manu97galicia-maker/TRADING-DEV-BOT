-- =====================================================================
-- B vs C: ¿que distingue al que SE DISPARA del que SE ESTANCA?
-- Mira las senales en los ~10s DESPUES de que cada moneda toca por 1a vez
-- la banda $3.5-4.5K. Compara B (se quedo <8K) vs C (paso 8K).
-- Si C tiene mas volumen/compradores en esa ventana -> esa es LA senal.
-- Ventana: 7 dias. SOL fijo $75 (para precision usar prices.usd real).
-- =====================================================================

WITH creates AS (
    SELECT mint, evt_block_time AS created
    FROM pumpdotfun_solana.pump_evt_CreateEvent
    WHERE evt_block_time >= NOW() - INTERVAL '7' day
),

priced AS (  -- MC de cada trade
    SELECT
        t.mint,
        t.evt_block_time AS ts,
        t.sol_amount,
        t.is_buy,
        t."user",
        (CAST(t.virtual_sol_reserves AS double) / 1e9)
        / NULLIF(CAST(t.virtual_token_reserves AS double) / 1e6, 0)
        * 1e9 * 75.0 AS mc
    FROM pumpdotfun_solana.pump_evt_TradeEvent t
    JOIN creates c ON c.mint = t.mint
),

band_touch AS (  -- primer instante que toca $3.5K
    SELECT mint, MIN(ts) AS band_ts
    FROM priced
    WHERE mc >= 3500
    GROUP BY mint
),

peak AS (
    SELECT mint, MAX(mc) AS peak_mc
    FROM priced
    GROUP BY mint
),

after_band AS (  -- metricas en los 10s SIGUIENTES a tocar la banda
    SELECT
        p.mint,
        SUM(CAST(p.sol_amount AS double)) / 1e9 AS vol_post,
        COUNT(*)                                AS trades_post,
        COUNT(DISTINCT p."user")                AS buyers_post,
        COUNT_IF(p.is_buy)                      AS buys_post
    FROM priced p
    JOIN band_touch b ON b.mint = p.mint
    WHERE p.ts BETWEEN b.band_ts AND b.band_ts + INTERVAL '10' second
    GROUP BY p.mint
),

joined AS (
    SELECT
        a.*,
        CASE WHEN pk.peak_mc >= 8000 THEN 'C: paso 8K' ELSE 'B: se estanco' END AS grupo
    FROM after_band a
    JOIN peak pk ON pk.mint = a.mint
)

SELECT
    grupo,
    COUNT(*)                                                       AS n_monedas,
    ROUND(APPROX_PERCENTILE(vol_post, 0.5), 1)                     AS vol_10s_post_mediana,
    ROUND(APPROX_PERCENTILE(buyers_post, 0.5))                     AS buyers_post_mediana,
    ROUND(APPROX_PERCENTILE(trades_post, 0.5))                     AS trades_post_mediana,
    ROUND(AVG(CASE WHEN trades_post > 0 THEN 100.0 * buys_post / trades_post END), 0) AS pct_buys_post,
    ROUND(APPROX_PERCENTILE(vol_post, 0.75), 1)                    AS vol_post_p75
FROM joined
GROUP BY grupo
ORDER BY grupo;
