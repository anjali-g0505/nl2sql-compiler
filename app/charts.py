"""Chart choice from the result's shape (config.yaml -> chart).

Runs after execution, because two of the rules need the result: "exactly one row" and
"how many categories". Rules, first match wins:

    0 dimensions and 1 metric       -> KPI
    exactly 1 row                   -> TABLE   (a lone bar says nothing)
    2+ dimensions or 2+ metrics     -> TABLE
    1 time dimension                -> LINE
    1 dimension                     -> BAR     (PIE is allowed if <= pie_max categories)

An explicit `AS <chart>` wins when the result can be drawn that way; otherwise the
inferred chart is used and a chart_fallback assumption says so.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from compiler.ast import Assumption
from compiler.semantic_layer import SemanticLayer


def choose_chart(
    layer: SemanticLayer,
    dimensions: Sequence[str],
    metrics: Sequence[str],
    rows: List[Dict[str, Any]],
    requested: Optional[str],
) -> Tuple[str, Optional[Assumption]]:
    """(chart type, fallback assumption or None) for a result."""
    inferred = _infer(layer, dimensions, metrics, rows)
    if not requested or requested == inferred:
        return inferred, None
    if _drawable(layer, requested, dimensions, metrics, rows):
        return requested, None
    return inferred, Assumption("chart_fallback", (("requested", requested), ("actual", inferred)))


def _infer(layer, dimensions, metrics, rows) -> str:
    if not dimensions and len(metrics) == 1:
        return "KPI"
    if len(rows) == 1 or len(dimensions) >= 2 or len(metrics) >= 2:
        return "TABLE"
    if layer.dimension(dimensions[0]).get("time_dimension"):
        return "LINE"
    return "BAR"


def _drawable(layer, chart, dimensions, metrics, rows) -> bool:
    if chart == "TABLE":
        return True
    if chart == "KPI":
        return len(metrics) == 1 and len(rows) == 1
    if len(dimensions) != 1 or not rows:
        return False
    if chart in ("BAR", "LINE"):
        return True
    if chart == "PIE":  # parts of one whole: one metric, few slices, nothing negative
        pie_max = layer.raw.get("chart", {}).get("pie_max_categories", 8)
        values = [row.get(metrics[0]) for row in rows] if len(metrics) == 1 else []
        return (
            len(metrics) == 1
            and len(rows) <= pie_max
            and all(v is not None and float(v) >= 0 for v in values)
        )
    return False
