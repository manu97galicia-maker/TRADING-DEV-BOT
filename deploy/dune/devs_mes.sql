-- DEVS DE PUMP.FUN DEL ULTIMO MES, por dev, con todo lo que pide Manu (7-nov).
-- Una sola consulta para no gastar creditos de Dune dos veces; los cortes se
-- aplican despues en deploy/dune_devs_mes.py (se pueden cambiar sin repetirla).
--
-- {SOL_USD} lo pone el script con el precio de SOL de hoy.
--
-- Definiciones (las mismas que explica el informe):
--   migro          la curva se vacio (real_token_reserves = 0 en algun trade)
--   pico           maximo market cap: curva de pump.fun y, si migro, PumpSwap
--                  (percentil 99 del precio, para que un trade raro no mande)
--   rug            el dev VENDIO (>= 0,1 SOL) en los primeros 30 min
--   mecha          el market cap se triplico en los primeros 10 s
--   misma_wallet   una sola cartera (que no es el dev) hizo > 30% de las compras
WITH creates AS (
    SELECT COALESCE(c.creator, c."user") AS dev, c.mint, c.evt_block_time AS creado
    FROM pumpdotfun_solana.pump_evt_createevent c
    WHERE c.evt_block_time >= NOW() - INTERVAL '30' day
),
trades AS (
    SELECT t.mint, t."user" AS wallet, t.is_buy, t.evt_block_time AS ts,
           CAST(t.sol_amount AS double) / 1e9 AS sol,
           (CAST(t.virtual_sol_reserves AS double) / 1e9)
             / NULLIF(CAST(t.virtual_token_reserves AS double) / 1e6, 0) * 1e9 AS mc_sol,
           CAST(t.real_token_reserves AS double) AS real_tok
    FROM pumpdotfun_solana.pump_evt_tradeevent t
    WHERE t.evt_block_time >= NOW() - INTERVAL '30' day
      AND t.mint IN (SELECT mint FROM creates)
),
por_token AS (
    SELECT c.dev, c.mint, c.creado,
           MAX(tr.mc_sol) * {SOL_USD} AS pico_curva_usd,
           MIN_BY(tr.mc_sol, tr.ts) AS mc_inicio,
           MAX(CASE WHEN tr.ts <= c.creado + INTERVAL '10' second THEN tr.mc_sol END) AS mc_10s,
           COUNT(*) AS trades,
           COUNT(DISTINCT tr.wallet) AS traders,
           MAX(CASE WHEN tr.real_tok = 0 THEN 1 ELSE 0 END) AS migro,
           SUM(CASE WHEN NOT tr.is_buy AND tr.wallet = c.dev
                     AND tr.ts <= c.creado + INTERVAL '30' minute THEN tr.sol ELSE 0 END)
             AS dev_vende_30m
    FROM creates c JOIN trades tr ON tr.mint = c.mint
    GROUP BY 1, 2, 3
),
compras_cartera AS (
    SELECT tr.mint, tr.wallet, SUM(tr.sol) AS s
    FROM trades tr JOIN creates c ON c.mint = tr.mint
    WHERE tr.is_buy AND tr.wallet <> c.dev
    GROUP BY 1, 2
),
concentracion AS (
    SELECT mint, MAX(s) / NULLIF(SUM(s), 0) AS top_share
    FROM compras_cartera GROUP BY 1
),
dex AS (
    SELECT CASE WHEN d.token_bought_mint_address IN (SELECT mint FROM creates)
                THEN d.token_bought_mint_address ELSE d.token_sold_mint_address END AS mint,
           APPROX_PERCENTILE(
             d.amount_usd / NULLIF(CASE WHEN d.token_bought_mint_address IN (SELECT mint FROM creates)
                                        THEN d.token_bought_amount ELSE d.token_sold_amount END, 0),
             0.99) * 1e9 AS pico_dex_usd,
           COUNT(*) AS trades_dex
    FROM dex_solana.trades d
    WHERE d.block_time >= NOW() - INTERVAL '30' day
      AND d.project = 'pumpswap'
      AND d.amount_usd >= 50
      AND (d.token_bought_mint_address IN (SELECT mint FROM creates)
           OR d.token_sold_mint_address IN (SELECT mint FROM creates))
    GROUP BY 1
),
tok AS (
    SELECT p.*, COALESCE(x.top_share, 0) AS top_share,
           GREATEST(p.pico_curva_usd, COALESCE(dx.pico_dex_usd, 0)) AS pico_usd,
           p.trades + COALESCE(dx.trades_dex, 0) AS trades_total,
           CASE WHEN p.dev_vende_30m >= 0.1 THEN 1 ELSE 0 END AS rug,
           CASE WHEN p.mc_10s >= 3 * p.mc_inicio THEN 1 ELSE 0 END AS mecha,
           CASE WHEN COALESCE(x.top_share, 0) > 0.30 THEN 1 ELSE 0 END AS misma_wallet
    FROM por_token p
    LEFT JOIN concentracion x ON x.mint = p.mint
    LEFT JOIN dex dx ON dx.mint = p.mint
),
moda AS (
    SELECT dev, MAX_BY(cubo, n) AS moda_usd
    FROM (SELECT dev, FLOOR(pico_usd / 10000) * 10000 AS cubo, COUNT(*) AS n
          FROM tok GROUP BY 1, 2) cubos
    GROUP BY 1
)
SELECT t.dev,
       COUNT(*) AS lanzamientos,
       COUNT(DISTINCT date_trunc('week', t.creado)) AS semanas_activas,
       COUNT_IF(t.creado >= NOW() - INTERVAL '7' day) AS lanz_7d,
       ROUND(100.0 * AVG(t.migro), 1) AS pct_migra,
       ROUND(APPROX_PERCENTILE(t.pico_usd, 0.5)) AS mediana_usd,
       MAX(m.moda_usd) AS moda_usd,
       ROUND(100.0 * COUNT_IF(t.pico_usd >= 20000) / COUNT(*), 1) AS frec_20k,
       ROUND(100.0 * COUNT_IF(t.pico_usd >= 80000) / COUNT(*), 1) AS frec_80k,
       ROUND(MAX(t.pico_usd)) AS max_pico_usd,
       COUNT_IF(t.pico_usd >= 300000) AS n_300k,
       COUNT_IF(t.pico_usd >= 1000000) AS n_1m,
       SUM(t.rug) AS rugs,
       SUM(t.mecha) AS mechas,
       SUM(t.misma_wallet) AS misma_wallet,
       ROUND(AVG(t.trades_total)) AS media_trades,
       ROUND(SUM(t.trades_total) / 30.0) AS trades_dia,
       SLICE(ARRAY_AGG(t.mint ORDER BY t.creado DESC), 1, 15) AS mints
FROM tok t LEFT JOIN moda m ON m.dev = t.dev
GROUP BY 1
HAVING COUNT(*) >= 4 OR MAX(t.pico_usd) >= 80000
