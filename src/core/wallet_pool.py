"""Multi-wallet pool: reparte las compras entre N wallets.

Carga las claves de wallets_generadas.json (o cualquier JSON con el mismo
formato). En cada compra elige la wallet con más SOL disponible que cubra
el importe; reserva el saldo bajo lock para que dos compras simultáneas no
pisen el mismo saldo. Al terminar la compra (bien o mal) hay que llamar a
release() para liberar la reserva.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from core.wallet import Wallet


class WalletPool:
    _BALANCE_TTL = 30.0  # re-lee saldos cada 30 s

    def __init__(self, json_path: str | Path) -> None:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        self._wallets: list[Wallet] = [
            Wallet(w["private_key"]) for w in data["wallets"]
        ]
        self._reserved: dict[int, int] = {}   # índice → lamports reservados
        self._balances: dict[int, int] = {}   # índice → saldo en lamports
        self._balance_ts: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def wallets(self) -> list[Wallet]:
        return self._wallets

    async def _refresh(self, client) -> None:
        for i, w in enumerate(self._wallets):
            try:
                self._balances[i] = await client.get_sol_balance(w.pubkey)
            except Exception:  # noqa: BLE001
                pass
        self._balance_ts = time.monotonic()

    async def pick(
        self,
        need_lamports: int,
        client,
    ) -> tuple[Wallet, int] | tuple[None, None]:
        """Devuelve (wallet, índice) con más SOL libre que cubra need_lamports.

        Reserva atómicamente bajo lock. Devuelve (None, None) si ninguna
        wallet tiene saldo suficiente.
        """
        async with self._lock:
            if time.monotonic() - self._balance_ts > self._BALANCE_TTL:
                await self._refresh(client)

            best_i, best_free = -1, -1
            for i in range(len(self._wallets)):
                free = self._balances.get(i, 0) - self._reserved.get(i, 0)
                if free >= need_lamports and free > best_free:
                    best_i, best_free = i, free

            if best_i < 0:
                return None, None

            self._reserved[best_i] = self._reserved.get(best_i, 0) + need_lamports
            return self._wallets[best_i], best_i

    def release(self, index: int, lamports: int) -> None:
        """Libera la reserva tras compra (éxito o fallo)."""
        self._reserved[index] = max(0, self._reserved.get(index, 0) - lamports)
        self._balances.pop(index, None)   # fuerza re-lectura en el próximo pick
        self._balance_ts = 0.0
