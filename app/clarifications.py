"""Where a query waits while the user answers a clarification.

A parked query has to outlive the request that created it, and — once more than one
worker or container is serving traffic — the process that created it. This module keeps
that state behind a small interface with two implementations: a dictionary for tests and
single-process runs, and Redis for anything else.

What is stored is deliberately **the DSL string, not the compiled query object**:

  * it is JSON, so nothing is pickled and a deploy mid-clarification cannot fail to
    deserialize an object written by the previous version of the code;
  * compilation is deterministic, so re-validating the DSL on the way back reproduces
    exactly the query that was parked;
  * it stays readable in Redis, which matters when something goes wrong at 2am.

Expiry belongs to the store: Redis does it with a TTL, the dictionary does it on access.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Protocol

KEY_PREFIX = "intentql:clarification:"


@dataclass(frozen=True)
class Pending:
    """A query waiting on the user, in a form that survives a restart."""

    question: Optional[str]
    dsl: str
    questions: List[Dict[str, Any]]
    attempts: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=_encode)

    @staticmethod
    def from_json(raw: str) -> "Pending":
        return Pending(**json.loads(raw))


def _encode(value: Any) -> Any:
    """Values that arrive from MySQL are not JSON by themselves.

    Amounts come back as Decimal and dates as date objects; a stored result has to
    survive the trip to Redis and read back as the same numbers, so they are converted
    here rather than left to fail at dump time.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


class Store(Protocol):
    """Somewhere a parked query can wait for the user to come back."""

    def put(self, key: str, pending: Pending, ttl: timedelta) -> None: ...

    def peek(self, key: str) -> Optional[Pending]:
        """Read without consuming — the answer may be incomplete and need asking again."""

    def take(self, key: str) -> Optional[Pending]:
        """Read and remove, atomically. Returns None if someone else got there first."""


@dataclass
class MemoryStore:
    """A dictionary. Correct for one process; every worker gets its own, so a
    clarification parked by one is invisible to the others."""

    clock: Any = datetime.now
    _items: Dict[str, tuple] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, key: str, pending: Pending, ttl: timedelta) -> None:
        with self._lock:
            self._purge()
            self._items[key] = (pending, self.clock() + ttl)

    def peek(self, key: str) -> Optional[Pending]:
        with self._lock:
            self._purge()
            found = self._items.get(key)
            return found[0] if found else None

    def take(self, key: str) -> Optional[Pending]:
        with self._lock:
            self._purge()
            found = self._items.pop(key, None)
            return found[0] if found else None

    def _purge(self) -> None:
        now = self.clock()
        for key in [k for k, (_, expires) in self._items.items() if expires <= now]:
            del self._items[key]


class RedisStore:
    """Redis, so any worker or container can answer a clarification another parked.

    Expiry is Redis's own TTL, and taking a key is a single atomic operation, so two
    clicks on the same clarification cannot both run the query.
    """

    def __init__(self, client: Any, prefix: str = KEY_PREFIX):
        self.client = client
        self.prefix = prefix

    @classmethod
    def from_url(cls, url: str, prefix: str = KEY_PREFIX) -> "RedisStore":
        import redis

        return cls(redis.Redis.from_url(url, decode_responses=True), prefix)

    def put(self, key: str, pending: Pending, ttl: timedelta) -> None:
        self.client.setex(self.prefix + key, int(ttl.total_seconds()), pending.to_json())

    def peek(self, key: str) -> Optional[Pending]:
        raw = self.client.get(self.prefix + key)
        return Pending.from_json(raw) if raw else None

    def take(self, key: str) -> Optional[Pending]:
        full_key = self.prefix + key
        getdel = getattr(self.client, "getdel", None)
        raw = getdel(full_key) if getdel else self._take_by_pipeline(full_key)
        return Pending.from_json(raw) if raw else None

    def _take_by_pipeline(self, full_key: str) -> Optional[str]:
        """GETDEL needs Redis 6.2; older servers get the same effect in one round trip."""
        pipeline = self.client.pipeline()
        pipeline.get(full_key)
        pipeline.delete(full_key)
        raw, _ = pipeline.execute()
        return raw
