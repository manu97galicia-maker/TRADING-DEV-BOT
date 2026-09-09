"""Read-only check for DexScreener, Birdeye, and DEXTools integrations."""

import argparse
import asyncio
from dataclasses import asdict

from dotenv import load_dotenv

from birdeye import BirdeyeClient
from dexscreener import DexScreenerClient
from dextools import DextoolsClient

SOL_MINT = "So11111111111111111111111111111111111111112"


async def main(token_address: str) -> None:
    """Fetch one token from every configured provider without trading."""
    load_dotenv()
    async with (
        DexScreenerClient() as dexscreener,
        BirdeyeClient() as birdeye,
        DextoolsClient() as dextools,
    ):
        dex_result, bird_result, tools_result = await asyncio.gather(
            dexscreener.get_market_data(token_address),
            birdeye.get_token_overview(token_address),
            dextools.get_token(token_address),
        )

    for name, result in (
        ("DexScreener", dex_result),
        ("Birdeye", bird_result),
        ("DEXTools", tools_result),
    ):
        print(f"{name}: {asdict(result) if result is not None else 'not available'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mint", nargs="?", default=SOL_MINT)
    args = parser.parse_args()
    asyncio.run(main(args.mint))
