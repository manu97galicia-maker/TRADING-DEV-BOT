# BSC Four.meme + Flap (paper)

This repository was originally Solana-only. The BSC strategy is isolated from
the Solana runner and has its own discovery, state and execution boundaries.
GMGN's read-only OpenAPI supplies Four.meme/Flap candidates and market data;
canonical launchpad contracts supply quotes and token taxes.

## Entry

- Platforms: Four.meme and Flap on BNB Chain (chain ID 56).
- Notional: USD 25, converted to BNB at the executable quote.
- Entry market cap must be strictly below USD 9,000.
- Token buy tax plus token sell tax must be at most 600 basis points (6%).
  Unknown or negative tax data is rejected. Protocol/DEX fees remain separate
  and are still included in paper P&L.
- A launch is rejected when its normalized name appeared on Four.meme or Flap
  less than two hours earlier. Comparison is case-insensitive, collapses spaces
  and normalizes Unicode. The shared history survives process restarts.
- Creator: at least two observed migrations and a migration rate of at least
  8% over all observed launches. Incomplete creator histories are rejected.

## Risk and exits

- Hard stop: 38% below the executed entry price.
- At +60%: sell 62.5% of the original tokens, recovering the original notional
  before fees.
- Sell 27.5% of the original position in six equal tranches at market caps of
  USD 10k, 15k, 20k, 25k, 30k and 35k.
- Keep 10%: sell 5% at USD 180k, 2.5% at USD 400k and 2.5% at USD 900k.

Triggers are monotonic and idempotent: a tier may execute only once. If market
cap jumps across tiers, every newly crossed tier must be accounted for.

## Gas

The bot reads the network gas price for each transaction. Ultra-fast buys use
2.5x and normal sells use 1.25x, both capped at 5 gwei. Gas estimates must use
the contract's estimate rather than a hard-coded transaction gas limit. A stop
loss may override the normal sell bid later when the live executor exists.
An entry is rejected when its estimated buy gas exceeds USD 1.30.

Four.meme's documented trading fee is 1%, with a minimum of 0.001 BNB. That
minimum must be included in paper P&L. It can exceed 1% on a USD 25 position.
DEX fees, token taxes, price impact, slippage, reverted transactions and every
individual exit's gas must also be included.

## Safety boundary

The coordinator persists processed tokens, positions and executed exit tiers,
so a restart cannot repeat a buy or an already completed tier. It monitors
exits before considering new entries and allows exactly two open positions;
there is no lifetime cap on the number of completed trades.

Before every real entry, the current BNB/USD price is used to preserve at least
USD 5 worth of BNB in the wallet. If the proposed USD 25 entry would cross that
reserve after also protecting the maximum USD 1.30 buy-gas allowance, it is
rejected.

The paper config never signs. The real template remains deliberately locked
with `paper_trade: true` and `live_transactions: false` until paper validation
is reviewed. Real execution additionally requires the exact environment
confirmation `I_UNDERSTAND_BSC_LIVE_TRADING`, a chain-56 RPC, canonical contract
bytecode, a locally loaded key, a successful simulation, bounded gas, a mined
successful receipt and a wallet-balance reconciliation.

## Configuration and launch

Secrets belong only in the repository-root file `/root/ai-bot/.env` on the
droplet (permissions `600`), never in YAML, chat, logs or Git:

```env
BSC_RPC_HTTP=https://your-private-bsc-rpc
GMGN_API_KEY=your-read-only-gmgn-api-key
BSC_PRIVATE_KEY=your-dedicated-low-balance-wallet-key
BSC_ENABLE_LIVE_TRADING=
```

Run one paper cycle first:

```bash
cd /root/ai-bot
PYTHONPATH=/root/ai-bot/src .venv/bin/python run_bsc.py \
  --config bots/bot-bsc-four-flap-paper.yaml --once
```

Continuous paper mode uses the same command without `--once`. Do not unlock
the real template until paper fills, taxes, quotes, restart recovery and every
exit tier have been observed successfully with the production RPC and GMGN
key.

Sources used for the integration: [GMGN OpenAPI CLI](https://github.com/GMGNAI/gmgn-skills),
[Four.meme integration](https://github.com/four-meme-community/fourmeme-ai),
and [Flap developer docs](https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers).
