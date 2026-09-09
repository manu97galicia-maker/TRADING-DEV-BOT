"""Preview a detailed Telegram buy notification without sending or trading."""

from monitoring.notifier import build_purchase_notification

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
        )
    )
