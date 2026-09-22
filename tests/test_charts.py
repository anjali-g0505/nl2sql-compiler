"""Chart choice from the result shape, per config.yaml -> chart and the grammar.md oracle."""
import pytest

from app.charts import choose_chart
from compiler.ast import Assumption
from compiler.semantic_layer import SemanticLayer

LAYER = SemanticLayer.load()


def rows(n, metric="value", value=1):
    return [{"iss_name": f"x{i}", metric: value} for i in range(n)]


@pytest.mark.parametrize("dims, metrics, result, expected", [
    ([], ["value"], rows(1), "KPI"),                                  # E1
    (["issuer"], ["value"], rows(3), "BAR"),                          # E2
    (["month"], ["volume"], rows(12, "volume"), "LINE"),              # E4
    (["customer"], ["volume"], rows(1, "volume"), "TABLE"),           # E5: one row
    (["issuer", "acquirer"], ["value"], rows(9), "TABLE"),            # E6
    (["merchant"], ["value"], rows(25), "BAR"),                       # E12
    (["country"], ["volume", "value", "ats"], rows(4), "TABLE"),      # E14
    ([], ["volume", "value"], rows(1), "TABLE"),                      # 0 dims, 2 metrics
])
def test_inferred_chart(dims, metrics, result, expected):
    assert choose_chart(LAYER, dims, metrics, result, None) == (expected, None)


def test_explicit_pie_is_kept_when_it_fits():                        # E7
    assert choose_chart(LAYER, ["issuer"], ["value"], rows(3), "PIE") == ("PIE", None)


@pytest.mark.parametrize("dims, metrics, result, requested, actual", [
    (["merchant"], ["value"], rows(25), "PIE", "BAR"),                # too many slices
    (["issuer"], ["value", "ats"], rows(3), "PIE", "TABLE"),          # two metrics
    (["issuer"], ["value"], rows(3, value=-5), "PIE", "BAR"),         # negative slice
    (["issuer"], ["value"], rows(3), "KPI", "BAR"),                   # several rows
    (["issuer", "acquirer"], ["value"], rows(9), "LINE", "TABLE"),
])
def test_incompatible_request_falls_back_with_an_assumption(dims, metrics, result, requested, actual):
    chart, fallback = choose_chart(LAYER, dims, metrics, result, requested)
    assert chart == actual
    assert fallback == Assumption("chart_fallback", (("requested", requested), ("actual", actual)))


def test_table_is_always_allowed():                                   # E18
    assert choose_chart(LAYER, ["txn"], ["value"], rows(5), "TABLE") == ("TABLE", None)


def test_bar_for_a_time_dimension_is_allowed():
    assert choose_chart(LAYER, ["month"], ["value"], rows(6), "BAR") == ("BAR", None)
