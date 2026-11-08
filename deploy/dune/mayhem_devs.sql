-- DEVS MAYHEM DE PUMP.FUN, FRESCOS (pedido el 8-nov: "consulta a Dune primero
-- si tiene mas o si esta actualizado; quiero unos 200 lanzamientos diarios si
-- puede ser"). Una sola consulta; los cortes se hacen en deploy/dune_mayhem.py.
--
-- {SOL_USD} lo pone el script con el precio de SOL de hoy (el estudio viejo,
-- mayhem_v2_raw.csv, lo tenia fijo en 150 $).
--
-- Por dev (2+ lanzamientos mayhem en 30 dias):
--   lanz_30d / lanz_7d / lanz_24h   lanzamientos mayhem
--   ultimo                          su ultimo lanzamiento mayhem
--   mediana_usd / max_usd           pico de market cap en la curva
--   pct_30k / pct_100k              % de sus monedas que llegaron ahi
--   pct_rug                         % en las que el dev vendio >= 0,1 SOL en
--                                   los primeros 30 min
-- Y en todas las filas, para saber si Dune esta al dia y cuanto mayhem hay:
--   ultimo_dato        ultima creacion de pump.fun que tiene Dune (cualquiera)
--   mayhem_24h / 7d    monedas mayhem lanzadas en total (todos los devs)
WITH creates AS (
    SELECT COALESCE(c.creator, c."user") AS dev, c.mint, c.evt_block_time AS creado
    FROM pumpdotfun_solana.pump_evt_createevent c
    WHERE c.evt_block_time >= NOW() - INTERVAL '30' day
      AND c.is_mayhem_mode = true
),
trades AS (
    SELECT t.mint, t."user" AS wallet, t.is_buy, t.evt_block_time AS ts,
           CAST(t.sol_amount AS double) / 1e9 AS sol,
           (CAST(t.virtual_sol_reserves AS double) / 1e9)
             / NULLIF(CAST(t.virtual_token_reserves AS double) / 1e6, 0) * 1e9 AS mc_sol
    FROM pumpdotfun_solana.pump_evt_tradeevent t
    WHERE t.evt_block_time >= NOW() - INTERVAL '30' day
      AND t.mint IN (SELECT mint FROM creates)
),
por_token AS (
    SELECT c.dev, c.mint, c.creado,
           COALESCE(MAX(tr.mc_sol), 0) * {SOL_USD} AS pico_usd,
           SUM(CASE WHEN NOT tr.is_buy AND tr.wallet = c.dev
                     AND tr.ts <= c.creado + INTERVAL '30' minute THEN tr.sol ELSE 0 END)
             AS dev_vende_30m
    FROM creates c LEFT JOIN trades tr ON tr.mint = c.mint
    GROUP BY 1, 2, 3
)
SELECT p.dev,
       COUNT(*) AS lanz_30d,
       COUNT_IF(p.creado >= NOW() - INTERVAL '7' day) AS lanz_7d,
       COUNT_IF(p.creado >= NOW() - INTERVAL '1' day) AS lanz_24h,
       MAX(p.creado) AS ultimo,
       ROUND(APPROX_PERCENTILE(p.pico_usd, 0.5)) AS mediana_usd,
       ROUND(MAX(p.pico_usd)) AS max_usd,
       ROUND(100.0 * COUNT_IF(p.pico_usd >= 30000) / COUNT(*), 1) AS pct_30k,
       ROUND(100.0 * COUNT_IF(p.pico_usd >= 100000) / COUNT(*), 1) AS pct_100k,
       ROUND(100.0 * COUNT_IF(p.dev_vende_30m >= 0.1) / COUNT(*), 1) AS pct_rug,
       (SELECT MAX(evt_block_time) FROM pumpdotfun_solana.pump_evt_createevent
        WHERE evt_block_time >= NOW() - INTERVAL '3' day) AS ultimo_dato,
       (SELECT COUNT(*) FROM creates WHERE creado >= NOW() - INTERVAL '1' day)
         AS mayhem_24h,
       (SELECT COUNT(*) FROM creates WHERE creado >= NOW() - INTERVAL '7' day)
         AS mayhem_7d
FROM por_token p
GROUP BY 1
HAVING COUNT(*) >= 2
