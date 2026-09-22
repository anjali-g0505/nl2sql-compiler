"""Value-resolution tests.

Enum dimensions resolve from config, so they need nothing external. Catalogue and
entity dimensions are driven by an index the caller supplies, so they are tested with
a small fake one — the same interface the database-backed index implements in stage 3.
"""
import pytest

from compiler.parser import parse
from compiler.resolver import (
    Candidate,
    ResolvedQuery,
    Status,
    build_index,
    normalize,
    resolve,
)
from compiler.validator import validate


def resolved(dsl, **kwargs):
    return resolve(validate(parse(dsl)), **kwargs)


def statuses(result):
    return [r.status for r in result.resolutions]


def values(result):
    return [c.value for c in result.query.ast.filters]


MERCHANTS = build_index(
    [("M001", "Big Bazaar"), ("M002", "Reliance Fresh"), ("M003", "Croma")],
    aliases={"M001": ["BigBazaar", "BB"]},
)
MCC = build_index([("5411", "Grocery Stores"), ("5812", "Restaurants")])


# --- normalization ------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Credit", "credit"),
    ("  Business   Decline  ", "business decline"),
    ("HDFC-Bank", "hdfc bank"),
    ("Café", "cafe"),
    ("D'Mart", "d mart"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# --- enum dimensions (config values) -----------------------------------------

@pytest.mark.parametrize("written, stored", [
    ("Credit", "Credit"),
    ("credit", "Credit"),
    ("CREDIT", "Credit"),
    ("  credit ", "Credit"),
])
def test_case_and_spacing_resolve_silently(written, stored):
    result = resolved(f"SHOW value WHERE card_type = '{written}'")
    assert statuses(result) == [Status.SUCCESS]
    assert values(result) == [stored]
    assert result.ok


def test_typo_is_corrected_and_surfaced():
    result = resolved("SHOW value WHERE card_type = 'Credt'")

    assert statuses(result) == [Status.CORRECTED]
    assert values(result) == ["Credit"]          # the SQL uses the real value
    assert result.ok                             # a correction does not stop the pipeline
    correction = result.corrections[0]
    assert (correction.raw, correction.resolved) == ("Credt", "Credit")


def test_ambiguous_value_asks_rather_than_guesses():
    result = resolved("SHOW volume WHERE status = 'decline'")

    assert statuses(result) == [Status.AMBIGUOUS]
    assert not result.ok
    names = {c.name for c in result.unresolved[0].candidates}
    assert names == {"Business Decline", "Technical Decline"}
    assert values(result) == ["decline"]          # left untouched until the user says


def test_unknown_value_is_reported_with_no_candidates():
    result = resolved("SHOW value WHERE card_type = 'xyzzy'")

    assert statuses(result) == [Status.UNKNOWN]
    assert result.unresolved[0].candidates == ()
    assert not result.ok


def test_short_values_are_never_fuzzy_matched():
    # 'Cr' must not quietly become 'Credit'
    result = resolved("SHOW value WHERE card_type = 'Cr'")
    assert statuses(result) == [Status.UNKNOWN]


def test_every_value_in_a_list_is_resolved():
    result = resolved("SHOW value WHERE card_type IN ('credit', 'DEBIT', 'Prepad')")

    assert statuses(result) == [Status.SUCCESS, Status.SUCCESS, Status.CORRECTED]
    assert values(result) == [("Credit", "Debit", "Prepaid")]


def test_not_equal_values_resolve_too():
    result = resolved("SHOW volume BY issuer WHERE status != 'succes'")
    assert values(result) == ["Success"]


def test_all_values_are_resolved_before_returning():
    dsl = "SHOW volume WHERE card_type = 'xyzzy' AND card_variant = 'nonsense'"
    assert statuses(resolved(dsl)) == [Status.UNKNOWN, Status.UNKNOWN]


def test_numeric_conditions_are_left_alone():
    result = resolved("SHOW value BY txn WHERE amount > 100000000")
    assert result.resolutions == ()
    assert result.ok


# --- catalogue and entity dimensions (index supplied by the caller) ----------

def test_catalog_resolves_text_to_the_filter_id():
    # mcc filters on m.mcc_code, so 'Grocery' must become '5411'
    result = resolved("SHOW value BY mcc WHERE mcc = 'Grocery'", indexes={"mcc": MCC})

    assert statuses(result) == [Status.CORRECTED]
    assert values(result) == ["5411"]


def test_catalog_accepts_the_code_itself():
    result = resolved("SHOW value WHERE mcc = '5411'", indexes={"mcc": MCC})
    assert statuses(result) == [Status.SUCCESS]
    assert values(result) == ["5411"]


def test_entity_resolves_to_the_name_because_that_is_the_filter_column():
    result = resolved(
        "SHOW value BY merchant WHERE merchant = 'big bazar'", indexes={"merchant": MERCHANTS}
    )
    assert statuses(result) == [Status.CORRECTED]
    assert values(result) == ["Big Bazaar"]


def test_entity_alias_matches_exactly():
    result = resolved(
        "SHOW value WHERE merchant = 'BigBazaar'", indexes={"merchant": MERCHANTS}
    )
    assert statuses(result) == [Status.SUCCESS]
    assert values(result) == ["Big Bazaar"]


def test_entity_id_matches_exactly():
    result = resolved("SHOW value WHERE merchant = 'M002'", indexes={"merchant": MERCHANTS})
    assert statuses(result) == [Status.SUCCESS]
    assert values(result) == ["Reliance Fresh"]


def test_unknown_entity_lists_nearest_matches_for_the_user():
    result = resolved(
        "SHOW value WHERE merchant = 'Relianse Freshh'", indexes={"merchant": MERCHANTS}
    )
    assert values(result) == ["Reliance Fresh"]   # one candidate -> corrected
    assert result.corrections[0].candidates[0].id == "M002"


def test_missing_index_skips_resolution_rather_than_failing():
    # stage 3 supplies these; until then the value passes through untouched
    result = resolved("SHOW value WHERE merchant = 'Big Bazaar'")
    assert statuses(result) == [Status.SKIPPED]
    assert values(result) == ["Big Bazaar"]
    assert result.ok


def test_id_dimensions_are_not_fuzzy_matched():
    result = resolved("SHOW volume BY customer WHERE customer = 'C000123'")
    assert statuses(result) == [Status.SKIPPED]
    assert values(result) == ["C000123"]


# --- the index itself ---------------------------------------------------------

def test_index_exact_lookup_is_normalized():
    assert MERCHANTS.lookup_exact(normalize("  big   bazaar ")).id == "M001"


def test_index_fuzzy_search_is_ordered_and_capped():
    matches = MERCHANTS.search_fuzzy(normalize("bazaar"), limit=2)
    assert len(matches) <= 2
    assert all(isinstance(m, Candidate) for m in matches)


def test_index_returns_nothing_when_nothing_is_close():
    assert MERCHANTS.search_fuzzy(normalize("zzzzzzzz"), limit=5) == []


# --- shape of the result ------------------------------------------------------

def test_the_rest_of_the_query_is_carried_through_unchanged():
    dsl = "SHOW value BY issuer WHERE card_type = 'credit' PERIOD MTD LIMIT 10 IN CRORE"
    validated = validate(parse(dsl))
    result = resolve(validated)

    assert isinstance(result, ResolvedQuery)
    assert result.query.joins == validated.joins
    assert result.query.success_filter == validated.success_filter
    assert result.query.ast.period == validated.ast.period
    assert result.query.ast.limit == 10
    assert result.query.ast.raw_dsl == dsl        # the original text always survives


def test_resolution_is_deterministic():
    dsl = "SHOW value WHERE card_type = 'Credt'"
    assert values(resolved(dsl)) == values(resolved(dsl))
