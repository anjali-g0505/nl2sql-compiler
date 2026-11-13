"""A tokens-per-minute budget per model, checked before every model call.

Groq's free tier allows ~8K tokens a minute per model, and a request over the limit
fails with a 429. Rather than find out from the 429 halfway through an investigation,
each node estimates what its call will cost and asks first. Input and output both count
(gpt-oss reasoning tokens are output), so estimates include the expected reply.

Estimates are characters / 4 — rough (±20%), which is why the limit is applied with a
little headroom rather than to the last token.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple

WINDOW_SECONDS = 60.0


def estimate(*texts: str) -> int:
    """Rough token count of some prompt text."""
    return sum(len(t or "") for t in texts) // 4


class TokenBudget:
    """Sliding one-minute window of tokens spent, per model. Thread-safe."""

    def __init__(self, limit_per_minute: int = 8000, headroom: float = 0.9,
                 clock: Callable[[], float] = time.monotonic):
        self.limit = int(limit_per_minute * headroom)
        self.clock = clock
        self._spent: Dict[str, Deque[Tuple[float, int]]] = {}
        self._lock = threading.Lock()

    def _window(self, model: str) -> Deque[Tuple[float, int]]:
        window = self._spent.setdefault(model, deque())
        cutoff = self.clock() - WINDOW_SECONDS
        while window and window[0][0] <= cutoff:
            window.popleft()
        return window

    def used(self, model: str) -> int:
        with self._lock:
            return sum(t for _, t in self._window(model))

    def try_spend(self, model: str, tokens: int) -> bool:
        """Record `tokens` against `model` if they fit in this minute; False if not."""
        with self._lock:
            window = self._window(model)
            if sum(t for _, t in window) + tokens > self.limit:
                return False
            window.append((self.clock(), tokens))
            return True

    def record(self, model: str, tokens: int) -> None:
        """Record tokens already spent (e.g. an unplanned retry), even over the limit."""
        with self._lock:
            self._window(model).append((self.clock(), tokens))

    def retry_after(self, model: str, tokens: int) -> int:
        """Seconds until `tokens` would fit: wait for the oldest spends to age out."""
        with self._lock:
            window = self._window(model)
            total = sum(t for _, t in window)
            wait = 0.0
            for stamp, spent in window:
                if total + tokens <= self.limit:
                    break
                total -= spent
                wait = stamp + WINDOW_SECONDS - self.clock()
            return max(1, math.ceil(wait))
