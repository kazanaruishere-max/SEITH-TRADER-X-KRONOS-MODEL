"""Rate limiter per-user (B4). In-memory, sliding-window sederhana.

Batas default: maks 10 perintah/menetap per menit per user. Ringan sekali;
hanya nyamankan banjir bot, bukan proteksi DDoS.
"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, max_calls: int = 10, window_seconds: int = 60) -> None:
        self._max = max_calls
        self._window = window_seconds
        self._hits: dict[int, list[float]] = {}

    def check(self, user_id: int | None) -> bool:
        """Return True bila LOLOS (bisa lanjut). False bila rate-limited."""
        if user_id is None:
            return True
        now = time.monotonic()
        cutoff = now - self._window
        dq = self._hits.setdefault(user_id, [])
        while dq and dq[0] < cutoff:
            dq.pop(0)
        if len(dq) >= self._max:
            return False
        dq.append(now)
        return True


_rate_limiter = RateLimiter()


def rate_limited(user_id: int | None) -> bool:
    """Return True bila user melebihi kuota (sebaiknya tolak)."""
    return not _rate_limiter.check(user_id)
