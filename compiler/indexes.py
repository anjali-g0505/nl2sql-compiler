"""Value indexes built from the database, for catalogue and entity dimensions.

The resolver matches filter values against an index; this module builds those indexes
from the SELECT DISTINCT queries declared in config.yaml and keeps them fresh:

    registry = IndexRegistry()            # uses config + app.db
    registry.refresh()                    # at startup
    resolve(validated, indexes=registry.as_mapping())

Design points that matter in operation:
  * A refresh builds NEW indexes and swaps them in with one assignment. Readers never
    see a half-built index, and no lock is needed on the read path.
  * A source that fails to rebuild keeps serving its previous index; the error is
    recorded and logged instead of taking the app down.
  * Every source records when it was last refreshed, so an UNKNOWN merchant can be
    reported as "merchant list last refreshed <time>" rather than a bare failure.
  * Nothing here knows what a merchant is: sources come from config, aliases from
    aliases.yaml, so a new entity is a config change.
  * The reference date ("today" for MTD, LAST n DAYS, ...) = MAX(date) is cached here
    too and refreshed in the same pass, since it only changes when the data does.
    Looking it up per query would let "MTD" shift mid-session for no benefit.

No database is required to use this module — `fetch` is injected, which is also how
the tests run it.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

import yaml

from compiler.resolver import ValueIndex, build_index
from compiler.semantic_layer import REPO_ROOT, SemanticLayer

logger = logging.getLogger(__name__)

Rows = Sequence[Mapping[str, object]]
Fetch = Callable[[str], Rows]
REFERENCE_DATE = "reference_date"  # its name in statuses and refresh(only=...)


@dataclass(frozen=True)
class SourceStatus:
    """What happened to one index the last time it was built."""

    name: str
    kind: str  # "catalog" | "entity" | "reference_date"
    rows: int = 0
    last_refreshed: Optional[datetime] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.last_refreshed is not None

    def describe_age(self, now: Optional[datetime] = None) -> str:
        """Human phrasing for UNKNOWN messages, e.g. 'last refreshed 3 hours ago'."""
        if self.last_refreshed is None:
            return "never refreshed"
        delta = (now or datetime.now()) - self.last_refreshed
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"last refreshed {int(delta.total_seconds() // 60)} minutes ago"
        if hours < 48:
            return f"last refreshed {int(hours)} hours ago"
        return f"last refreshed on {self.last_refreshed:%Y-%m-%d}"


@dataclass
class RefreshReport:
    """The outcome of one refresh pass, for logs and the admin endpoint."""

    started: datetime
    finished: datetime
    statuses: Tuple[SourceStatus, ...] = ()

    @property
    def ok(self) -> bool:
        return all(status.ok for status in self.statuses)

    @property
    def failures(self) -> Tuple[SourceStatus, ...]:
        return tuple(s for s in self.statuses if not s.ok)


def default_fetch(sql: str) -> Rows:
    """Run a query through the app's MySQL pool (imported late: tests need no DB)."""
    from app.db import execute_query

    return execute_query(sql)


def load_aliases(layer: Optional[SemanticLayer] = None) -> Mapping[str, Mapping[str, Sequence[str]]]:
    """aliases.yaml -> {entity: {id: [alias, ...]}}; a missing file is not an error."""
    layer = layer or SemanticLayer.load()
    name = layer.raw.get("value_resolution", {}).get("aliases_file")
    if not name:
        return {}
    path = Path(name)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        logger.info("No aliases file at %s; continuing without aliases", path)
        return {}
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class IndexRegistry:
    """Holds one index per catalogue/entity source, and rebuilds them atomically."""

    def __init__(
        self,
        layer: Optional[SemanticLayer] = None,
        fetch: Fetch = default_fetch,
        aliases: Optional[Mapping] = None,
    ):
        self.layer = layer or SemanticLayer.load()
        self.fetch = fetch
        self._aliases = aliases if aliases is not None else load_aliases(self.layer)
        self._indexes: Dict[str, ValueIndex] = {}
        self._statuses: Dict[str, SourceStatus] = {}
        self._reference_date: Optional[date] = None
        self._write_lock = threading.Lock()  # one refresh at a time; reads stay lock-free

    # --- sources declared in config -------------------------------------------

    @property
    def sources(self) -> Mapping[str, Tuple[str, str]]:
        """{name: (kind, sql)} for every catalogue and entity in config."""
        section = self.layer.raw.get("value_resolution", {})
        found: Dict[str, Tuple[str, str]] = {}
        for kind, block in (("catalog", "catalogs"), ("entity", "entities")):
            for name, sql in section.get(block, {}).items():
                found[str(name)] = (kind, str(sql))
        return found

    # --- reading --------------------------------------------------------------

    def get(self, name: str) -> Optional[ValueIndex]:
        return self._indexes.get(name)

    def as_mapping(self) -> Mapping[str, ValueIndex]:
        """What resolve(indexes=...) takes. A snapshot: later refreshes don't mutate it."""
        return dict(self._indexes)

    def status(self, name: Optional[str] = None):
        if name is not None:
            return self._statuses.get(name)
        return dict(self._statuses)

    # --- refreshing -----------------------------------------------------------

    def refresh(self, only: Optional[str] = None) -> RefreshReport:
        """Rebuild every source (or one), swapping in each new index atomically.

        Called at startup, nightly, and by hand when new merchants are onboarded.
        A source that fails keeps its previous index.
        """
        started = datetime.now()
        statuses = []
        with self._write_lock:
            for name, (kind, sql) in self.sources.items():
                if only and name != only:
                    continue
                statuses.append(self._refresh_one(name, kind, sql))
            if not only or only == REFERENCE_DATE:
                statuses.append(self._refresh_reference_date())
        report = RefreshReport(started=started, finished=datetime.now(), statuses=tuple(statuses))
        if report.failures:
            logger.warning(
                "Index refresh finished with %d failure(s): %s",
                len(report.failures),
                ", ".join(f"{s.name}: {s.error}" for s in report.failures),
            )
        return report

    def _refresh_one(self, name: str, kind: str, sql: str) -> SourceStatus:
        try:
            rows = self.fetch(sql)
            pairs = [(str(row["id"]), str(row["name"])) for row in rows]
        except Exception as exc:  # DB down, bad query, unexpected columns
            logger.exception("Could not rebuild the %r index", name)
            previous = self._statuses.get(name)
            status = SourceStatus(
                name=name,
                kind=kind,
                rows=previous.rows if previous else 0,
                last_refreshed=previous.last_refreshed if previous else None,
                error=f"{type(exc).__name__}: {exc}",
            )
            self._statuses[name] = status
            return status

        index = build_index(pairs, aliases=self._aliases.get(name), layer=self.layer)
        self._indexes[name] = index  # one assignment: readers see old or new, never partial
        status = SourceStatus(name=name, kind=kind, rows=len(pairs), last_refreshed=datetime.now())
        self._statuses[name] = status
        return status

    # --- reference date -------------------------------------------------------

    @property
    def reference_date(self) -> Optional[date]:
        """'Today' for relative periods: config's pinned date, else the cached MAX(date).

        Refreshed with the indexes (startup, nightly, admin endpoint), i.e. whenever
        the data may have changed, rather than looked up on every query.
        """
        pinned = self.layer.raw.get("reference_date")
        if pinned:
            return _to_date(pinned)
        return self._reference_date

    def _refresh_reference_date(self) -> SourceStatus:
        name, kind = REFERENCE_DATE, REFERENCE_DATE
        if self.layer.raw.get("reference_date"):  # pinned in config: nothing to query
            status = SourceStatus(name=name, kind=kind, rows=1, last_refreshed=datetime.now())
            self._statuses[name] = status
            return status

        fact = self.layer.raw.get("fact", {})
        sql = f"SELECT MAX({fact.get('date_column', 'date')}) AS ref FROM {fact.get('table')}"
        try:
            rows = self.fetch(sql)
            value = rows[0]["ref"] if rows else None
            if value is None:
                raise ValueError(f"{fact.get('table')} has no transactions")
            self._reference_date = _to_date(value)
        except Exception as exc:  # keep the previous date, as a failed index does
            logger.exception("Could not refresh the reference date")
            previous = self._statuses.get(name)
            status = SourceStatus(
                name=name,
                kind=kind,
                rows=previous.rows if previous else 0,
                last_refreshed=previous.last_refreshed if previous else None,
                error=f"{type(exc).__name__}: {exc}",
            )
            self._statuses[name] = status
            return status

        status = SourceStatus(name=name, kind=kind, rows=1, last_refreshed=datetime.now())
        self._statuses[name] = status
        return status


def _to_date(value: object) -> date:
    """A DATE, DATETIME or 'YYYY-MM-DD...' string -> date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# --- nightly schedule ---------------------------------------------------------

def seconds_until(hour: int, minute: int = 0, now: Optional[datetime] = None) -> float:
    """Seconds from now to the next occurrence of hour:minute (pure, so it's testable)."""
    now = now or datetime.now()
    target = datetime.combine(now.date(), time(hour, minute))
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def refresh_nightly(registry: IndexRegistry, hour: int = 2, minute: int = 0) -> None:
    """Background task: refresh every night at hour:minute, forever.

    Started from the app's lifespan. Failures are logged by refresh() and never stop
    the loop, so one bad night doesn't leave the indexes stale for good.
    """
    import asyncio

    while True:
        await asyncio.sleep(seconds_until(hour, minute))
        try:
            await asyncio.to_thread(registry.refresh)
        except Exception:  # refresh handles its own errors; this is the last resort
            logger.exception("Nightly index refresh failed")
