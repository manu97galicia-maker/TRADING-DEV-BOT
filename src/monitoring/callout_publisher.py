"""Publish confirmed-position callouts through the visible Pump.fun form.

The publication journal is separate from trading state. A submission with an
unknown outcome is never automatically repeated. No swap or wallet signing API
is used here; a dedicated browser profile must be signed in by its owner.
"""

from __future__ import annotations

# Optional browser imports stay lazy; recovery errors must not expose sessions.
# Explicit rejection branches mirror the admission checks.
# ruff: noqa: PLC0415, TRY003, BLE001, PLR0911, C901
import asyncio
import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from utils.logger import get_logger

logger = get_logger(__name__)
ORIGIN = "https://pump.fun"
MAX_MESSAGE_LENGTH = 2000

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

RpcReader = Callable[[str, list], Awaitable[dict | None]]


def event_id(entry: dict) -> str:
    identity = [entry["publisher_account"], entry["mint"], entry["buy_signature"]]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def fresh(entry: dict, maximum_age: float) -> bool:
    try:
        stamp = datetime.fromisoformat(entry["created_at"])
        return (
            stamp.tzinfo is not None
            and 0 <= time.time() - stamp.timestamp() <= maximum_age
        )
    except (KeyError, TypeError, ValueError):
        return False


def pending_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and all(
            isinstance(entry.get(k), str) and entry[k]
            for k in ("mint", "buy_signature", "publisher_account", "message")
        ):
            entries.append(entry)
    return entries


class PublicationJournal:
    """Durably claim before clicking; never retry an unknown submission."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS publications ("
                "event TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "account TEXT NOT NULL, mint TEXT NOT NULL, buy_signature TEXT NOT NULL, "
                "updated REAL NOT NULL, url TEXT, reason TEXT)"
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=2)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def status(self, entry: dict) -> str | None:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT status FROM publications WHERE event=?", (event_id(entry),)
            ).fetchone()
        return row[0] if row else None

    def claim(self, entry: dict, status: str = "sending", reason: str = "") -> bool:
        with closing(self.connect()) as db, db:
            return (
                db.execute(
                    "INSERT OR IGNORE INTO publications VALUES (?,?,?,?,?,?,NULL,?)",
                    (
                        event_id(entry),
                        status,
                        entry["publisher_account"],
                        entry["mint"],
                        entry["buy_signature"],
                        time.time(),
                        reason,
                    ),
                ).rowcount
                == 1
            )

    def published(self, entry: dict, url: str) -> None:
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc, parsed.path) != (
            "https",
            "pump.fun",
            f"/coin/{entry['mint']}",
        ) or not re.fullmatch(r"highlight_callout=[a-zA-Z0-9-]+", parsed.query):
            raise ValueError("A visible callout permalink is required")
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE publications SET status='published',url=?,updated=?,reason=NULL "
                "WHERE event=? AND status='sending'",
                (url, time.time(), event_id(entry)),
            )


class PositionVerifier:
    """Read the shared ledger and chain; never construct/sign/send transactions."""

    def __init__(self, ledger: Path, owner: str, rpc: RpcReader) -> None:
        self.ledger, self.owner, self.rpc = ledger, owner, rpc

    async def held(self, entry: dict) -> bool:
        from solders.pubkey import Pubkey
        from solders.signature import Signature

        Pubkey.from_string(entry["mint"])
        Signature.from_string(entry["buy_signature"])
        if not self.ledger.exists():
            return False
        with closing(
            sqlite3.connect(self.ledger.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            owner = db.execute("SELECT owner FROM settings WHERE id=1").fetchone()
            receipt = db.execute(
                "SELECT p.state FROM positions p JOIN receipts r ON r.nonce=p.nonce "
                "WHERE p.mint=? AND r.signature=? AND r.kind='buy'",
                (entry["mint"], entry["buy_signature"]),
            ).fetchone()
        if not owner or owner[0] != self.owner or not receipt or receipt[0] != "open":
            return False
        tx = await self.rpc(
            "getTransaction",
            [
                entry["buy_signature"],
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        if not tx or not tx.get("meta") or tx["meta"].get("err") is not None:
            return False
        keys = tx["transaction"]["message"]["accountKeys"]
        if not any(
            isinstance(k, dict) and k.get("pubkey") == self.owner and k.get("signer")
            for k in keys
        ):
            return False

        def amount(rows: list) -> int:
            return sum(
                int(r["uiTokenAmount"]["amount"])
                for r in rows
                if r.get("owner") == self.owner and r.get("mint") == entry["mint"]
            )

        if amount(tx["meta"].get("postTokenBalances", [])) <= amount(
            tx["meta"].get("preTokenBalances", [])
        ):
            return False
        holdings = await self.rpc(
            "getTokenAccountsByOwner",
            [
                self.owner,
                {"mint": entry["mint"]},
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "minContextSlot": tx["slot"],
                },
            ],
        )
        if int(holdings["context"]["slot"]) < int(tx["slot"]):
            return False
        return (
            sum(
                int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
                for a in holdings["value"]
            )
            > 0
        )


class PumpfunForm:
    """Only interact with observed form controls; never click a Buy button."""

    def __init__(self, page: Page, account: str, owner: str) -> None:
        self.page, self.account, self.owner = page, account, owner

    async def identity(self) -> None:
        await self.page.goto(f"{ORIGIN}/profile/{self.account}")
        await self.page.get_by_role(
            "button", name=re.compile(r"^(Cuenta|Account)$")
        ).click()
        identity = self.page.get_by_test_id("topbar-user-view-profile")
        if await identity.get_attribute("href") != f"/profile/{self.account}":
            raise ValueError("Publisher account does not match the authorized account")
        text = await identity.inner_text()
        if f"{self.owner[:4]}…{self.owner[-4:]}" not in text:
            raise ValueError("Connected wallet does not match the configured wallet")
        await self.page.get_by_role(
            "button", name=re.compile(r"^(Cuenta|Account)$")
        ).click()
        # The public profile exposes the full address in its avatar alt text.
        await self.page.get_by_role(
            "img", name=re.compile(re.escape(self.owner))
        ).wait_for()
        await self.page.get_by_role(
            "button", name=re.compile(r"^(Editar perfil|Edit profile)$")
        ).wait_for()

    async def prepare(self, entry: dict) -> Locator | None:
        await self.identity()
        await self.page.get_by_role(
            "button", name=re.compile(r"^(Create|Crear)$")
        ).click()
        await self.page.get_by_role("menuitem", name="Callout", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        await dialog.get_by_role("searchbox").fill(entry["mint"])
        image = self.page.get_by_role(
            "img", name=f"Coin image for {entry['mint']}", exact=True
        )
        await dialog.get_by_role("button").filter(has=image).click()
        await dialog.get_by_role("textbox").fill(entry["message"])
        button = dialog.get_by_role(
            "button", name=re.compile(r"^(Llamar a \$|Call out \$|Callout \$)")
        )
        await button.wait_for(timeout=15000)
        if not await button.is_enabled():
            await dialog.get_by_role(
                "button", name=re.compile(r"^(Cerrar|Close)$")
            ).click()
            return None
        return button

    async def confirm(self, entry: dict) -> str | None:
        # A hidden composer or success toast alone is insufficient evidence.
        await self.page.goto(f"{ORIGIN}/coin/{entry['mint']}")
        articles = (
            self.page.get_by_role("article")
            .filter(has=self.page.locator(f'a[href="/profile/{self.account}"]'))
            .filter(has_text=entry["message"])
        )
        try:
            await articles.first.wait_for(timeout=15000)
            link = articles.first.locator('a[href*="highlight_callout="]')
            href = await link.first.get_attribute("href")
            return urljoin(ORIGIN, href) if href else None
        except Exception:
            return None


class CalloutPublisher:
    def __init__(
        self,
        settings: dict,
        journal: PublicationJournal,
        verifier: PositionVerifier,
        form: PumpfunForm,
    ) -> None:
        self.settings, self.journal, self.verifier, self.form = (
            settings,
            journal,
            verifier,
            form,
        )

    async def publish_one(self, entry: dict) -> str:
        if self.journal.status(entry):
            return "already_handled"
        if entry["publisher_account"] != self.settings["callout_account"]:
            self.journal.claim(entry, "skipped", "different_account")
            return "different_account"
        if len(entry["message"]) > MAX_MESSAGE_LENGTH:
            self.journal.claim(entry, "skipped", "message_too_long")
            return "message_too_long"
        max_age = self.settings["callout_publication"]["max_age_seconds"]
        if not fresh(entry, max_age):
            self.journal.claim(entry, "skipped", "expired")
            return "expired"
        if not await self.verifier.held(entry):
            return "no_verified_position"
        button = await self.form.prepare(entry)
        if button is None:
            return "form_not_eligible"
        # Verify again after navigation and search, immediately before sending.
        if not fresh(entry, max_age) or not await self.verifier.held(entry):
            return "position_or_signal_changed"
        if not self.journal.claim(entry):
            return "already_handled"
        # From this point, any crash or timeout leaves 'sending' for manual review.
        # It must never cause a second click after a restart.
        await button.click()
        await self.form.page.get_by_role("dialog").wait_for(
            state="hidden", timeout=15000
        )
        url = await self.form.confirm(entry)
        if not url:
            return "submission_unverified"
        self.journal.published(entry, url)
        logger.info("Callout published on @%s: %s", entry["publisher_account"], url)
        return "published"


async def browser_service(
    settings: dict, rpc_endpoint: str, *, setup: bool = False
) -> None:
    """Dedicated visible Chrome session, manually authenticated, separate from TP/SL."""
    import aiohttp
    from playwright.async_api import async_playwright

    publication = settings["callout_publication"]
    profile = Path(publication["profile_directory"]).resolve()
    marker = profile / "publisher-ready.json"
    if not setup and not marker.exists():
        logger.warning(
            "Callout publisher needs manual setup: run_callout_publisher.py --setup"
        )
        return
    account, owner = settings["callout_account"], settings["expected_wallet"]
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            channel="chrome",
            headless=False,
            # Allow wallet extensions the user installs in this dedicated profile.
            ignore_default_args=["--disable-extensions"],
        )
        try:
            page = await context.new_page()
            page.set_default_timeout(15000)
            form = PumpfunForm(page, account, owner)
            if setup:
                await page.goto(ORIGIN)
                print(
                    f"Inicia sesión tú en Pump.fun como @{account}. No pegues claves en esta consola."
                )
                await asyncio.to_thread(
                    input, "Después de entrar, pulsa Enter aquí para verificar: "
                )
                await form.identity()
                marker.write_text(
                    json.dumps({"account": account, "wallet": owner}), encoding="utf-8"
                )
                print(
                    "Sesión del publicador verificada. Ya puedes arrancar el publicador."
                )
                return
            await form.identity()
            journal = PublicationJournal(Path(publication["state_database"]))
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:

                async def rpc(method: str, params: list) -> dict | None:
                    if method not in {"getTransaction", "getTokenAccountsByOwner"}:
                        raise ValueError("Publisher only permits read RPC methods")
                    async with session.post(
                        rpc_endpoint,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": method,
                            "params": params,
                        },
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()
                    if data.get("error"):
                        raise ValueError("Publisher RPC evidence unavailable")
                    return data.get("result")

                verifier = PositionVerifier(Path(settings["database"]), owner, rpc)
                publisher = CalloutPublisher(settings, journal, verifier, form)
                while True:
                    for entry in pending_entries(Path(settings["callout_outbox"])):
                        try:
                            result = await publisher.publish_one(entry)
                            if result == "submission_unverified":
                                logger.warning(
                                    "Callout submission unverified; automatic repost blocked"
                                )
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:
                            logger.warning(
                                "Callout pending/manual review (%s)",
                                type(error).__name__,
                            )
                    await asyncio.sleep(publication["poll_interval_seconds"])
        finally:
            await context.close()


async def optional_browser_service(settings: dict, rpc_endpoint: str) -> None:
    try:
        await browser_service(settings, rpc_endpoint)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Never print browser errors that might include local session data.
        logger.warning(
            "Callout publisher unavailable (%s); trading monitors continue",
            type(error).__name__,
        )
