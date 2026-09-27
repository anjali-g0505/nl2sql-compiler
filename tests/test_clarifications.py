"""Clarification store tests: the dictionary, and Redis against a stand-in client.

The Redis tests use a small fake rather than a running server, so they check the calls
this code makes (SETEX with a TTL, an atomic take) without needing infrastructure.
"""
from datetime import datetime, timedelta

import pytest

from app.clarifications import KEY_PREFIX, MemoryStore, Pending, RedisStore

TTL = timedelta(minutes=30)


def pending(dsl="SHOW volume BY issuer WHERE status = 'decline'"):
    return Pending(
        question="declined volume by issuer",
        dsl=dsl,
        questions=[{"id": "q1", "field": "status", "value": "decline",
                    "reason": "ambiguous", "message": "which one?", "options": []}],
        attempts=1,
    )


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, 0)

    def __call__(self):
        return self.now


class FakeRedis:
    """Enough of redis-py to check what the store asks for."""

    def __init__(self, supports_getdel=True):
        self.values = {}
        self.ttls = {}
        self.supports_getdel = supports_getdel
        if supports_getdel:
            self.getdel = self._getdel

    def setex(self, key, seconds, value):
        self.values[key] = value
        self.ttls[key] = seconds

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)

    def _getdel(self, key):
        return self.values.pop(key, None)

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def get(self, key):
        self.calls.append(("get", key))

    def delete(self, key):
        self.calls.append(("delete", key))

    def execute(self):
        results = []
        for command, key in self.calls:
            results.append(self.client.get(key) if command == "get" else self.client.delete(key))
        return results


# --- the payload ---------------------------------------------------------------

def test_a_pending_query_survives_a_round_trip_through_json():
    original = pending()
    assert Pending.from_json(original.to_json()) == original


def test_only_the_dsl_is_stored_not_a_compiled_object():
    # JSON, so a deploy mid-clarification cannot fail to deserialize the old shape
    raw = pending().to_json()
    assert "SHOW volume BY issuer" in raw
    assert "ValidatedQuery" not in raw and "QueryAST" not in raw


# --- both stores behave the same -------------------------------------------------

@pytest.fixture(params=["memory", "redis"])
def store(request):
    return MemoryStore() if request.param == "memory" else RedisStore(FakeRedis())


def test_put_then_peek_returns_it_without_consuming(store):
    store.put("abc", pending(), TTL)
    assert store.peek("abc") == pending()
    assert store.peek("abc") == pending()        # still there


def test_take_consumes_it(store):
    store.put("abc", pending(), TTL)
    assert store.take("abc") == pending()
    assert store.take("abc") is None             # a second click gets nothing
    assert store.peek("abc") is None


def test_unknown_keys_are_none(store):
    assert store.peek("nope") is None and store.take("nope") is None


def test_keys_do_not_collide(store):
    store.put("one", pending("SHOW value"), TTL)
    store.put("two", pending("SHOW volume"), TTL)
    assert store.take("one").dsl == "SHOW value"
    assert store.peek("two").dsl == "SHOW volume"


# --- expiry ---------------------------------------------------------------------

def test_memory_store_forgets_after_the_ttl():
    clock = Clock()
    store = MemoryStore(clock=clock)
    store.put("abc", pending(), TTL)
    clock.now += timedelta(minutes=29)
    assert store.peek("abc") is not None
    clock.now += timedelta(minutes=2)
    assert store.peek("abc") is None


def test_redis_store_hands_expiry_to_redis():
    client = FakeRedis()
    RedisStore(client).put("abc", pending(), TTL)
    assert client.ttls[KEY_PREFIX + "abc"] == 1800     # seconds, set with SETEX
    assert KEY_PREFIX + "abc" in client.values


# --- redis details ---------------------------------------------------------------

def test_take_uses_getdel_when_the_server_has_it():
    client = FakeRedis(supports_getdel=True)
    store = RedisStore(client)
    store.put("abc", pending(), TTL)
    assert store.take("abc") is not None          # single atomic call
    assert client.values == {}


def test_take_falls_back_to_a_pipeline_on_older_servers():
    client = FakeRedis(supports_getdel=False)     # GETDEL needs Redis 6.2
    store = RedisStore(client)
    store.put("abc", pending(), TTL)
    assert store.take("abc") is not None
    assert client.values == {}


def test_a_prefix_keeps_the_keyspace_tidy():
    client = FakeRedis()
    RedisStore(client, prefix="other:").put("abc", pending(), TTL)
    assert list(client.values) == ["other:abc"]


# --- values that come from the database -------------------------------------------

def test_database_values_survive_the_round_trip():
    """Rows arrive from MySQL holding Decimal and date objects, which plain json.dumps
    refuses. A stored result has to come back as the same numbers."""
    from datetime import date
    from decimal import Decimal

    result = Pending(
        question="value by issuer",
        dsl="SHOW value BY issuer",
        questions=[{"rows": [{"iss_name": "A bank", "value": Decimal("236070.37"),
                              "day": date(2025, 12, 31)}]}],
        attempts=1,
    )
    restored = Pending.from_json(result.to_json())
    row = restored.questions[0]["rows"][0]
    assert row["value"] == 236070.37 and isinstance(row["value"], float)
    assert row["day"] == "2025-12-31"
