-- =====================================================================
-- TOP 50 proyectos por VOLUMEN en los primeros 5 MINUTOS (+ su dev)
-- Volumen temprano alto = demanda/liquidez inmediata = puedes entrar Y salir
-- sin quedarte atascado. Ventana: ultimos 7 dias.
-- =====================================================================

WITH creates AS (
    SELECT
        COALESCE(c.creator, c."user") AS dev,
        c.mint,
        c.symbol,
        c.evt_block_time AS created
    FROM pumpdotfun_solana.pump_evt_CreateEvent c
    WHERE c.evt_block_time >= NOW() - INTERVAL '7' day
),

early AS (
    SELECT
        c.dev, c.mint, c.symbol, c.created,
        SUM(CAST(t.sol_amount AS double)) / 1e9   AS vol5m_sol,   -- SOL movidos en los 1os 5 min
        COUNT(*)                                  AS trades5m,
        COUNT(DISTINCT t."user")                  AS traders5m    -- compradores distintos
    FROM creates c
    JOIN pumpdotfun_solana.pump_evt_TradeEvent t
      ON t.mint = c.mint
     AND t.evt_block_time BETWEEN c.created AND c.created + INTERVAL '5' minute
    GROUP BY c.dev, c.mint, c.symbol, c.created
)

SELECT
    symbol,
    dev                       AS wallet,
    ROUND(vol5m_sol, 1)       AS vol_5min_sol,
    trades5m,
    traders5m,
    created,
    mint
FROM early
ORDER BY vol5m_sol DESC
LIMIT 50;

-- ---------------------------------------------------------------------
-- VARIANTE (por DEV, para ver quien REPITE volumen temprano alto):
-- Sustituye el SELECT final por esto:
--   SELECT dev AS wallet, COUNT(*) AS launches,
--       ROUND(APPROX_PERCENTILE(vol5m_sol,0.5),1) AS median_vol5m_sol,
--       ROUND(MAX(vol5m_sol),1) AS max_vol5m_sol
--   FROM early GROUP BY dev HAVING COUNT(*) >= 3
--   ORDER BY median_vol5m_sol DESC LIMIT 50;
-- ---------------------------------------------------------------------
