"""Token bucket rate limiter for Solana RPC requests."""

import asyncio
import math
import time

from utils.logger import get_logger

logger = get_logger(__name__)

#: Cada cuanto se resume en el log lo que ha frenado el tope propio.
_CADA_CUANTO_AVISA = 60.0


class TokenBucketRateLimiter:
    """Token bucket rate limiter for controlling RPC request rate.

    Implements a token bucket algorithm that replenishes tokens at a
    fixed rate. Each RPC call consumes one token. When the bucket is
    empty, callers wait until a token becomes available.

    Args:
        max_rps: Maximum requests per second (bucket refill rate). Must be positive.
        burst_size: Maximum burst size (bucket capacity). Defaults to max_rps.
    """

    def __init__(self, max_rps: float, burst_size: int | None = None) -> None:
        if max_rps <= 0:
            msg = f"max_rps must be positive, got {max_rps}"
            raise ValueError(msg)
        self._max_rps = max_rps
        self._burst_size = (
            burst_size if burst_size is not None else max(1, math.ceil(max_rps))
        )
        if self._burst_size <= 0:
            msg = f"burst_size must be positive, got {burst_size}"
            raise ValueError(msg)
        self._tokens = float(self._burst_size)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        #: CUANTO NOS FRENA NUESTRO PROPIO TOPE. Esto no es el nodo diciendo
        #: que no: es el bot esperando por un limite que le hemos puesto
        #: nosotros. Si `max_rps` esta por debajo de lo que da el plan
        #: contratado, el bot se frena solo y desde fuera parece que el nodo
        #: va lento. No habia forma de verlo.
        self.esperas = 0
        self.segundos_esperando = 0.0
        self._ultimo_aviso = time.monotonic()

    async def acquire(self) -> None:
        """Acquire a token, waiting if the bucket is empty."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_time = (1.0 - self._tokens) / self._max_rps
                self.esperas += 1
                self.segundos_esperando += wait_time
                self._avisa_si_toca()

            await asyncio.sleep(wait_time)

    def _avisa_si_toca(self) -> None:
        """Deja constancia en el log, como mucho una vez por minuto.

        Solo habla cuando de verdad se esta esperando, para no ensuciar el log
        cuando todo va holgado.
        """
        ahora = time.monotonic()
        if ahora - self._ultimo_aviso < _CADA_CUANTO_AVISA:
            return
        transcurrido = ahora - self._ultimo_aviso
        self._ultimo_aviso = ahora
        logger.warning(
            "NODO: el tope propio de %.0f consultas/s ha hecho esperar %d veces "
            "(%.1f s en total) en los ultimos %.0f s. Si el plan contratado da "
            "mas, subir `node.max_rps` quita este freno.",
            self._max_rps, self.esperas, self.segundos_esperando, transcurrido,
        )
        self.esperas = 0
        self.segundos_esperando = 0.0

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._burst_size,
            self._tokens + elapsed * self._max_rps,
        )
        self._last_refill = now
