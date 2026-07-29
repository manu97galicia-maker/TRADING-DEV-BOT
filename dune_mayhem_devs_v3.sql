-- =====================================================================
-- MAYHEM devs v3 — CON FILTRO ANTI-GRANJA
-- El fallo de v1/v2: medían solo los lanzamientos que calificaban, sin ver
-- el MURO de basura. Una granja (ej. 138.000 tokens, 0.76% migran) se colaba
-- con "buena mediana" porque Dune solo veía sus 2 mayhem con suerte.
--
-- v3 cuenta TODOS los lanzamientos del dev (30d) y su tasa de migración, y
-- DESCARTA las granjas: total_launches <= 60 (max ~2/dia) Y migran >= 5%.
-- Añade columnas total_launches y pct_migrated para que las VEAS.
-- =====================================================================

WITH all_creates AS (            -- TODOS los lanzamientos del dev (mayhem o no)
    SELECT
        COALESCE(c.creator, c."user") AS dev,
        c.mint,
        c.is_mayhem_mode
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

-- Totales por dev: cuántos lanza y qué % llegan a migrar (~$69K completa curva).
dev_totals AS (
    SELECT
        a.dev,
        COUNT(*)                                            AS total_launches,
        COUNT_IF(a.is_mayhem_mode)                          AS mayhem_launches,
        100.0 * COUNT_IF(COALESCE(p.peak_mc_usd,0) >= 65000) / COUNT(*) AS pct_migrated
    FROM all_creates a
    LEFT JOIN token_peak p ON p.mint = a.mint
    GROUP BY a.dev
),

-- Solo los mayhem, con su pico, para las stats de calidad.
mayhem_joined AS (
    SELECT a.dev, a.mint, COALESCE(p.peak_mc_usd,0) AS peak
    FROM all_creates a
    LEFT JOIN token_peak p ON p.mint = a.mint
    WHERE a.is_mayhem_mode = true
),
mayhem_stats AS (
    SELECT
        dev,
        APPROX_PERCENTILE(peak, 0.5)                        AS median_peak,   -- P50 (típico)
        APPROX_PERCENTILE(peak, 0.25)                       AS p25_peak,      -- P25 (SUELO = baja varianza)
        APPROX_PERCENTILE(peak, 0.10)                       AS p10_peak,      -- P10 (peor caso típico)
        MAX(peak)                                           AS best_peak,
        100.0 * COUNT_IF(peak >= 20000)  / COUNT(*)         AS hit_20k,       -- hit-rate: % que llega a $20K
        100.0 * COUNT_IF(peak >= 30000)  / COUNT(*)         AS pct_30k,
        100.0 * COUNT_IF(peak >= 100000) / COUNT(*)         AS pct_100k
    FROM mayhem_joined
    GROUP BY dev
)

SELECT
    t.dev                          AS wallet,
    t.total_launches,
    t.mayhem_launches,
    ROUND(t.pct_migrated, 1)       AS pct_migrated,
    ROUND(m.median_peak)           AS median_peak,
    ROUND(m.p25_peak)              AS p25_peak,       -- <<< el suelo: consistencia / baja varianza
    ROUND(m.p10_peak)              AS p10_peak,
    ROUND(m.hit_20k, 1)            AS hit_rate_20k,   -- <<< % de mayhem que llegan a $20K
    ROUND(m.best_peak)             AS best_peak_usd,
    ROUND(m.pct_100k, 1)           AS pct_pump_100k
FROM dev_totals t
JOIN mayhem_stats m ON m.dev = t.dev
WHERE t.total_launches <= 60             -- NO granjas: max ~2 lanz/dia
  AND t.pct_migrated  >= 5.0             -- al menos 5% de TODOS sus tokens llegan lejos
  AND t.mayhem_launches >= 2
  AND m.median_peak   >= 20000
  AND m.p25_peak      >= 20000           -- <<< BAJA VARIANZA: 75% de sus mayhem llegan a $20K
ORDER BY m.p25_peak DESC, m.pct_100k DESC
LIMIT 300;
