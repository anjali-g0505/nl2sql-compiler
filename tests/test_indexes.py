"""Index registry tests: building, atomic refresh, failure handling, scheduling.

No database: `fetch` is injected, so these run anywhere. The fake stands in for
app.db.execute_query and returns the same shape (a list of column->value dicts).
"""
from datetime import date, datetime, timedelta

import pytest
import yaml

from compiler.indexes import (
    IndexRegistry,
    SourceStatus,
    load_aliases,
    seconds_until,
)
from compiler.parser import parse
from compiler.resolver import Status, normalize, resolve
from compiler.semantic_layer import SemanticLayer
from compiler.validator import validate

MERCHANT_ROWS = [
    {"id": "MER0001", "name": "FreshBasket Supermart"},
    {"id": "MER0002", "name": "Spice Route Kitchen"},
]
MCC_ROWS = [
    {"id": "5411", "name": "Grocery Stores and Supermarkets"},
    {"id": "5812", "name": "Eating Places and Restaurants"},
]


class FakeDB:
    """Stands in for app.db.execute_query; records calls and can be made to fail."""

    def __init__(self, rows_by_table=None, fail_on=(), max_date="2025-12-27"):
        self.rows_by_table = rows_by_table or {}
        self.fail_on = set(fail_on)
        self.max_date = max_date  # what SELECT MAX(date) returns; None = empty table
        self.queries = []

    def __call__(self, sql):
        self.queries.append(sql)
        if "MAX(" in sql:
            if "reference_date" in self.fail_on:
                raise RuntimeError("card_txns is unavailable")
            return [{"ref": self.max_date}]
        for table, rows in self.rows_by_table.items():
            if table in sql:
                if table in self.fail_on:
                    raise RuntimeError(f"{table} is unavailable")
                return rows
        return []


@pytest.fixture
def layer():
    return SemanticLayer.load()


@pytest.fixture
def registry(layer):
    fake = FakeDB({"merchant_master": MERCHANT_ROWS, "response_master": [], "card_txns": []})
    return IndexRegistry(layer=layer, fetch=fake, aliases={})


# --- what config declares -----------------------------------------------------

def test_sources_come_from_config(registry):
    sources = registry.sources
    assert set(sources) >= {"mcc", "response", "country", "location",
                            "issuer", "acquirer", "merchant"}
    assert sources["merchant"][0] == "entity"
    assert sources["mcc"][0] == "catalog"


def test_nothing_is_built_before_the_first_refresh(registry):
    assert registry.as_mapping() == {}
    assert registry.get("merchant") is None


# --- building -----------------------------------------------------------------

def test_refresh_builds_every_source(registry):
    report = registry.refresh()

    assert report.ok
    assert registry.get("merchant") is not None
    assert registry.status("merchant").rows == 2
    assert registry.status("merchant").last_refreshed is not None


def test_built_index_resolves_real_values(layer):
    fake = FakeDB({"merchant_master": MERCHANT_ROWS})
    registry = IndexRegistry(layer=layer, fetch=fake, aliases={})
    registry.refresh()

    result = resolve(
        validate(parse("SHOW value BY merchant WHERE merchant = 'freshbasket supermart'")),
        indexes=registry.as_mapping(),
    )
    assert [r.status for r in result.resolutions] == [Status.SUCCESS]
    assert [c.value for c in result.query.ast.filters] == ["FreshBasket Supermart"]


def test_catalog_index_maps_text_to_the_code(layer):
    fake = FakeDB({"merchant_master": MCC_ROWS})  # the mcc catalogue reads this table
    registry = IndexRegistry(layer=layer, fetch=fake, aliases={})
    registry.refresh("mcc")

    result = resolve(
        validate(parse("SHOW value BY mcc WHERE mcc = 'grocery'")),
        indexes=registry.as_mapping(),
    )
    assert [c.value for c in result.query.ast.filters] == ["5411"]


def test_aliases_are_merged_into_the_index(layer):
    fake = FakeDB({"merchant_master": MERCHANT_ROWS})
    registry = IndexRegistry(
        layer=layer, fetch=fake, aliases={"merchant": {"MER0001": ["FreshBasket", "FB"]}}
    )
    registry.refresh()

    assert registry.get("merchant").lookup_exact(normalize("FreshBasket")).id == "MER0001"


def test_refresh_one_source_only(registry):
    report = registry.refresh("merchant")
    assert [s.name for s in report.statuses] == ["merchant"]
    assert registry.get("mcc") is None


# --- failures -----------------------------------------------------------------

def test_a_failed_source_keeps_the_previous_index(layer):
    fake = FakeDB({"merchant_master": MERCHANT_ROWS})
    registry = IndexRegistry(layer=layer, fetch=fake, aliases={})
    registry.refresh("merchant")
    before = registry.get("merchant")

    fake.fail_on.add("merchant_master")          # the database goes away
    report = registry.refresh("merchant")

    assert not report.ok
    assert registry.get("merchant") is before    # still serving the old index
    assert "unavailable" in registry.status("merchant").error
    assert registry.status("merchant").last_refreshed is not None  # the old time is kept


def test_a_failure_does_not_stop_other_sources(layer):
    fake = FakeDB(
        {"merchant_master": MERCHANT_ROWS, "issuer_master": [{"id": "I1", "name": "HDFC Bank"}]},
        fail_on=["merchant_master"],
    )
    registry = IndexRegistry(layer=layer, fetch=fake, aliases={})
    report = registry.refresh()

    assert {s.name for s in report.failures} == {"mcc", "merchant"}   # both read that table
    assert registry.get("issuer") is not None


def test_unresolvable_source_leaves_values_untouched(layer):
    registry = IndexRegistry(layer=layer, fetch=FakeDB(fail_on=[]), aliases={})
    registry.refresh()

    result = resolve(
        validate(parse("SHOW value WHERE merchant = 'Whatever'")),
        indexes=registry.as_mapping(),
    )
    # an empty index is still an index: nothing matches, so the user is asked
    assert [r.status for r in result.resolutions] == [Status.UNKNOWN]
    assert [c.value for c in result.query.ast.filters] == ["Whatever"]


# --- snapshots and status -----------------------------------------------------

def test_as_mapping_is_a_snapshot(registry):
    registry.refresh("merchant")
    snapshot = registry.as_mapping()
    registry.refresh("mcc")

    assert "mcc" not in snapshot        # a query in flight keeps the indexes it started with
    assert "mcc" in registry.as_mapping()


@pytest.mark.parametrize("minutes_ago, expected", [
    (5, "5 minutes ago"),
    (60 * 3, "3 hours ago"),
    (60 * 24 * 5, "on "),
])
def test_status_age_is_reportable(minutes_ago, expected):
    now = datetime(2026, 1, 10, 12, 0)
    status = SourceStatus(
        name="merchant", kind="entity", rows=2,
        last_refreshed=now - timedelta(minutes=minutes_ago),
    )
    assert expected in status.describe_age(now)


def test_never_refreshed_is_reported_as_such():
    assert SourceStatus("merchant", "entity").describe_age() == "never refreshed"


# --- aliases file -------------------------------------------------------------

def test_real_aliases_file_loads():
    aliases = load_aliases()
    assert aliases.get("issuer", {}).get("ISS002") == ["HDFC"]


def test_missing_aliases_file_is_not_an_error(tmp_path):
    document = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    document["value_resolution"]["aliases_file"] = str(tmp_path / "nope.yaml")
    assert load_aliases(SemanticLayer.from_dict(document)) == {}


# --- nightly schedule ---------------------------------------------------------

@pytest.mark.parametrize("now, expected_hours", [
    (datetime(2026, 1, 10, 1, 0), 1),      # before 02:00 today
    (datetime(2026, 1, 10, 2, 0), 24),     # exactly 02:00 -> tomorrow
    (datetime(2026, 1, 10, 23, 0), 3),     # after 02:00 -> tomorrow
])
def test_seconds_until_next_run(now, expected_hours):
    assert seconds_until(2, 0, now) == expected_hours * 3600


# --- reference date -----------------------------------------------------------

def test_reference_date_is_cached_by_refresh(layer):
    fake = FakeDB(max_date="2025-12-27")
    registry = IndexRegistry(layer=layer, fetch=fake, aliases={})
    assert registry.reference_date is None           # nothing until the first refresh

    report = registry.refresh()

    assert registry.reference_date == date(2025, 12, 27)
    assert registry.status("reference_date").ok
    assert "reference_date" in {s.name for s in report.statuses}
    assert sum("MAX(" in q for q in fake.queries) == 1  # once per refresh, not per query


def test_reference_date_accepts_a_datetime(layer):
    fake = FakeDB(max_date=datetime(2025, 12, 27, 23, 59))
    registry = IndexRegistry(layer=layer, fetch=fake, aliases={})
    registry.refresh(only="reference_date")
    assert registry.reference_date == date(2025, 12, 27)


def test_failed_reference_date_keeps_the_previous_one(layer):
    fake = FakeDB(max_date="2025-12-27")
    registry = IndexRegistry(layer=layer, fetch=fake, aliases={})
    registry.refresh()

    fake.fail_on.add("reference_date")
    report = registry.refresh()

    assert [s.name for s in report.failures] == ["reference_date"]
    assert registry.reference_date == date(2025, 12, 27)


def test_empty_fact_table_is_a_reference_date_failure(layer):
    registry = IndexRegistry(layer=layer, fetch=FakeDB(max_date=None), aliases={})
    report = registry.refresh()
    assert "no transactions" in registry.status("reference_date").error
    assert registry.reference_date is None
    assert not report.ok


def test_refreshing_one_index_skips_the_reference_date(layer):
    fake = FakeDB({"merchant_master": MCC_ROWS})
    registry = IndexRegistry(layer=layer, fetch=fake, aliases={})
    registry.refresh("mcc")
    assert not any("MAX(" in q for q in fake.queries)


def test_pinned_reference_date_is_never_queried():
    document = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    document["reference_date"] = "2025-06-30"
    fake = FakeDB(max_date="2025-12-27")
    registry = IndexRegistry(layer=SemanticLayer.from_dict(document), fetch=fake, aliases={})
    registry.refresh()
    assert registry.reference_date == date(2025, 6, 30)
    assert not any("MAX(" in q for q in fake.queries)
