"""Retry value indexes that failed to build at startup.

The app builds its value indexes once at startup and then nightly. If MySQL is not up
yet at startup, every source fails, and until the nightly refresh the resolver has no
issuer, merchant or response index: it passes filter values through unmatched, so a
typo like 'HSBS bank' reaches the database as is and the answer is empty (shown as 0)
instead of "Interpreted HSBS bank as HSBC Bank".

That happened for real: after Docker Desktop restarted, the app container came back
22 seconds before MySQL (a restart policy does not honour depends_on). This task closes
the gap: it retries only the sources that failed, with growing pauses, until all of
them are built or the schedule runs out (the nightly refresh then takes over).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, List, Sequence

logger = logging.getLogger(__name__)

# ~8 minutes in total: enough for MySQL to finish starting, even on a cold Docker start
RETRY_DELAYS = (5, 10, 15, 30, 60, 60, 60, 60, 60, 60, 60)


def failed_sources(registry: Any) -> List[str]:
    """Names of the sources whose last refresh ended in an error."""
    return [name for name, status in registry.status().items() if status.error]


async def retry_failed_sources(registry: Any, delays: Sequence[float] = RETRY_DELAYS,
                               sleep: Callable = asyncio.sleep) -> bool:
    """Rebuild failed sources until none are left. True if all are built in the end."""
    for delay in delays:
        failed = failed_sources(registry)
        if not failed:
            return True
        logger.warning("value indexes not built (%s); retrying in %ss", ", ".join(failed), delay)
        await sleep(delay)
        for name in failed:
            await asyncio.to_thread(registry.refresh, name)
    remaining = failed_sources(registry)
    if remaining:
        logger.error("value indexes still not built after retries: %s; the nightly refresh "
                     "or POST /admin/refresh-indexes will rebuild them", ", ".join(remaining))
    else:
        logger.info("value indexes built after retrying")
    return not remaining
