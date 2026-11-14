"""Startup index retry: failed sources are rebuilt until MySQL answers."""
import asyncio
from types import SimpleNamespace

from agent.recovery import failed_sources, retry_failed_sources


class FlakyRegistry:
    """Sources fail until the database 'comes up' after `fail_for` refresh attempts."""

    def __init__(self, fail_for):
        self.fail_for = fail_for
        self.refreshed = []
        self._status = {name: SimpleNamespace(error="Could not connect to the database.")
                        for name in ("issuer", "merchant")}
        self._status["mcc"] = SimpleNamespace(error=None)

    def status(self):
        return dict(self._status)

    def refresh(self, name):
        self.refreshed.append(name)
        if len(self.refreshed) > self.fail_for:
            self._status[name] = SimpleNamespace(error=None)


async def no_sleep(_):
    return None


def test_failed_sources_are_retried_until_built():
    registry = FlakyRegistry(fail_for=2)          # the first round fails, the second works
    assert failed_sources(registry) == ["issuer", "merchant"]
    assert asyncio.run(retry_failed_sources(registry, delays=(1, 1, 1), sleep=no_sleep))
    assert registry.refreshed == ["issuer", "merchant", "issuer", "merchant"]   # never mcc
    assert failed_sources(registry) == []


def test_nothing_to_do_when_everything_built():
    registry = FlakyRegistry(fail_for=0)
    registry._status = {"issuer": SimpleNamespace(error=None)}
    assert asyncio.run(retry_failed_sources(registry, delays=(1,), sleep=no_sleep))
    assert registry.refreshed == []


def test_gives_up_after_the_schedule_and_says_so():
    registry = FlakyRegistry(fail_for=100)
    assert not asyncio.run(retry_failed_sources(registry, delays=(1, 1), sleep=no_sleep))
    assert failed_sources(registry) == ["issuer", "merchant"]
