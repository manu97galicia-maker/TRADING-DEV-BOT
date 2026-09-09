"""Preview a detailed Telegram buy notification without sending or trading."""

import sys

from monitoring.notifier import build_purchase_notification

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if __name__ == "__main__":
    print(
        build_purchase_notification(
            symbol="EXAMPLE",
            mint="ExamplePumpMint",
            amount_sol=0.20,
            source_data={
                "acquisition_source": "dexscreener",
                "dexscreener_boosts": 500,
            },
            market_cap_usd=12_500,
            strategy="+25%/70% · +50%/10% · +75%/10% · +200%/10%",
            stop_loss_pct=18,
            entry_price_sol=0.000000123456,
            take_profit_tiers=[
                (0.25, 0.70),
                (0.50, 0.10),
                (0.75, 0.10),
                (2.00, 0.10),
            ],
        )
    )
