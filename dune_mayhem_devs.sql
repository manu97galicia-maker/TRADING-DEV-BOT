-- =====================================================================
-- MEJORES DEVS DE MAYHEM (pump.fun) — Dune
-- Devs cuyos lanzamientos MAYHEM se DISPARAN (alto pico + buen hit-rate).
-- Ventana: ultimos 14 dias. Pega el resultado (CSV/Export) y te armo la lista.
--
-- ⚠️ VERIFICA 3 cosas en tu schema (los nombres pueden variar segun la
--    version del ABI decodificado en Dune):
--    1) tabla de creaciones: pumpdotfun_solana.pump_evt_CreateEvent
--    2) columna del flag mayhem: is_mayhem_mode  (si no existe, ver nota abajo)
--    3) tabla de trades: pumpdotfun_solana.pump_evt_TradeEvent
-- =====================================================================

WITH mayhem_creates AS (
    SELECT
        c.mint,
        COALESCE(c.creator, c."user") AS dev,
        c.evt_block_time            AS created_at
    FROM pumpdotfun_solana.pump_evt_CreateEvent c
    WHERE c.evt_block_time >= NOW() - INTERVAL '14' day
      AND c.is_mayhem_mode = true          -- <<< FLAG MAYHEM (verifica el nombre)
),

-- Pico de market cap por token, calculado desde las reservas de la curva
-- en cada trade. MC ≈ (SOL_reserves / token_reserves) * supply(1B) * precio_SOL.
token_peak AS (
    SELECT
        t.mint,
        MAX(
            (CAST(t.virtual_sol_reserves AS double) / 1e9)
            / NULLIF(CAST(t.virtual_token_reserves AS double) / 1e6, 0)
            * 1e9        -- supply total pump.fun = 1,000,000,000
            * 150.0      -- $/SOL (ajusta al precio del momento)
        ) AS peak_mc_usd
    FROM pumpdotfun_solana.pump_evt_TradeEvent t
    GROUP BY t.mint
),

joined AS (
    SELECT
        m.dev,
        m.mint,
        COALESCE(p.peak_mc_usd, 0) AS peak_mc_usd
    FROM mayhem_creates m
    LEFT JOIN token_peak p ON p.mint = m.mint
)

SELECT
    dev                                                        AS wallet,
    COUNT(*)                                                   AS mayhem_launches,
    ROUND(APPROX_PERCENTILE(peak_mc_usd, 0.5))                 AS median_peak,
    ROUND(MAX(peak_mc_usd))                                    AS best_peak_usd,
    ROUND(100.0 * COUNT_IF(peak_mc_usd >= 30000)  / COUNT(*), 1) AS pct_pump_30k,
    ROUND(100.0 * COUNT_IF(peak_mc_usd >= 100000) / COUNT(*), 1) AS pct_pump_100k
FROM joined
GROUP BY dev
HAVING COUNT(*) >= 3                                   -- min 3 mayhem (evita suerte)
   AND APPROX_PERCENTILE(peak_mc_usd, 0.5) >= 25000    -- mediana pico >= $25K
ORDER BY pct_pump_100k DESC, median_peak DESC
LIMIT 100;

-- ---------------------------------------------------------------------
-- NOTA si NO existe la columna is_mayhem_mode en el CreateEvent decodificado:
--   pump.fun la mete en create_v2. Si tu tabla no la trae, usa la tabla de
--   instrucciones decodificadas (pumpdotfun_solana.pump_call_create_v2) y filtra
--   por su argumento is_mayhem_mode, o quita el filtro mayhem para sacar TODOS
--   los devs y luego lo cruzamos. Dime qué tablas te salen y lo ajusto.
-- ---------------------------------------------------------------------
