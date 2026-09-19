"""AST and pipeline types for the DSL defined in grammar.md.

These are the data shapes the stages bind to:

    parser     -> QueryAST        faithful transcript of the DSL, nothing inferred
    validator  -> ValidatedQuery  names resolved, joins computed; codegen's only input
    codegen    -> CompiledQuery   SQL plus the assumptions that fired

Every dataclass is frozen and every collection is a tuple, so no stage can mutate a
query it was handed. Stages build new instances instead (e.g. dataclasses.replace).

This module holds types only: no parsing, alias resolution, join resolution, SQL
generation or config.yaml loading.
"""
from __future__ import annotations  # lets `int | None` annotations work on Python 3.9

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

COMPARISON_OPS = frozenset({"=", "!=", ">", ">=", "<", "<="})
TEXT_OPS = frozenset({"=", "!="})  # a single text value
LIST_OPS = frozenset({"IN", "NOT IN"})  # a tuple of text values


@dataclass(frozen=True)
class Period:
    """The PERIOD clause.
    One field on QueryAST holds the whole clause, so a query with two periods cannot
    be represented. `n` is set only for `PERIOD LAST n DAYS`; `start`/`end` only for
    `PERIOD FROM 'YYYY-MM-DD' TO 'YYYY-MM-DD'`.

    RANGE dates are `datetime.date`, not strings: the parser converts them, so a
    malformed date can never reach the AST, and codegen emits `date.isoformat()`, so
    nothing but a well-formed date literal can reach the SQL. Both ends are inclusive.
    """

    kind: str  #"FTD" | "WTD" | "MTD" | "QTD" | "YTD" | "LAST_N_DAYS" | "RANGE"
    n: int | None = None
    start: date | None = None
    end: date | None = None

    def __post_init__(self) -> None:
        if self.kind == "LAST_N_DAYS" and self.n is None:
            raise ValueError("Period kind LAST_N_DAYS requires n")
        if self.n is not None and self.kind != "LAST_N_DAYS":
            raise ValueError(f"Period kind {self.kind!r} does not take n (got n={self.n})")
        if self.kind == "RANGE":
            if self.start is None or self.end is None:
                raise ValueError("Period kind RANGE requires start and end")
            if self.start > self.end:
                raise ValueError(f"Period start {self.start} is after end {self.end}")
        elif self.start is not None or self.end is not None:
            raise ValueError(f"Period kind {self.kind!r} does not take start/end")


@dataclass(frozen=True)
class Name:
    """A metric, dimension or attribute reference, in both typed and resolved forms.

    `raw` is exactly what the user/LLM wrote, original case preserved, for error
    messages and the audit panel. `canonical` is the config.yaml key codegen looks up.

    The parser sets canonical == raw; it does no resolution. The validator produces
    new Names whose canonical is the resolved key, e.g. raw="category" ->
    canonical="mcc".
    """

    raw: str
    canonical: str


@dataclass(frozen=True)
class Condition:
    """One WHERE condition on a row-level field, before aggregation. Joined by AND.

    Three shapes, told apart by the value's type (the parser can't know yet whether a
    name is a dimension or an attribute; the validator checks that against config):
    - `field = 'text'` / `field != 'text'`       a dimension, e.g. card_type != 'Prepaid'
    - `field IN ('a', 'b')` / `NOT IN (...)`     a dimension, value is a tuple of text
    - `field <op> number`                        a numeric attribute, e.g. amount > 100000000

    A money attribute's threshold is in the query's display unit, like HAVING: with
    `IN CRORE`, `amount > 10` means more than 10 crore. Numbers are Decimal, not
    float, so the literal prints back exactly in the SQL. IN lists keep the order
    written, duplicates included: the AST is a transcript.
    """

    field: Name
    op: str  # text: "=" | "!="; list: "IN" | "NOT IN"; number: any of COMPARISON_OPS
    value: str | Decimal | tuple[str, ...]  # str: the literal without its quotes

    def __post_init__(self) -> None:
        if self.op in LIST_OPS:
            if not isinstance(self.value, tuple) or not self.value:
                raise ValueError(f"{self.op} needs a non-empty tuple of text values")
            if not all(isinstance(v, str) for v in self.value):
                raise ValueError(f"{self.op} lists hold text values only")
        elif self.op not in COMPARISON_OPS:
            raise ValueError(f"Unknown comparison operator {self.op!r}")
        elif isinstance(self.value, tuple):
            raise ValueError(f"A list of values needs IN or NOT IN, not {self.op!r}")
        elif isinstance(self.value, str) and self.op not in TEXT_OPS:
            raise ValueError(f"Text values only support '=' and '!=', not {self.op!r}")


@dataclass(frozen=True)
class MetricCondition:
    """One HAVING condition: `metric <op> number`. Conditions are joined by AND.

    HAVING filters aggregated metrics after grouping; row-level fields (dimensions,
    attributes) are filtered with WHERE (Condition), never here.

    `value` is in the query's display unit: with `IN CRORE`, `HAVING value > 3` means
    more than 3 crore, because codegen compares against the unit-scaled metric alias.
    It is a Decimal, not a float, so the literal prints back exactly in the SQL.
    """

    metric: Name
    op: str  # "=" | "!=" | ">" | ">=" | "<" | "<="
    value: Decimal

    def __post_init__(self) -> None:
        if self.op not in COMPARISON_OPS:
            raise ValueError(f"Unknown comparison operator {self.op!r}")


@dataclass(frozen=True)
class QueryAST: #this class is just to check the structure of the query and to make sure that the query is valid. It does not perform any validation or code generation.
    #so that when the object is created, it can be used to check the structure of the query and to make sure that the query is valid
    #used when the query is parsed and the AST is created.
    """The parser's output: a faithful transcript of what was written.

    The AST records intent; defaults and policy are applied downstream. In particular:

    - Collections are tuples, not lists, so the frozen dataclass is genuinely
      immutable (a frozen dataclass holding a list can still have the list mutated).
    - `limit` stays None when the user gave no LIMIT. Codegen applies the
      forced-limit guardrail and emits the `forced_limit` assumption; the AST must
      preserve "user gave nothing" so that can happen.
    - `order_by` reflects ONLY an explicit ORDER BY clause. Default orderings for
      some dimensions (e.g. `month` sorted chronologically) are applied later by
      codegen and are not recorded here.
    - `chart_type` comes from the AS clause and is never used to build SQL.
    - `filters` (WHERE) hold row-level conditions (dimensions, numeric attributes);
      `having` holds aggregated-metric conditions. Every money threshold, in WHERE
      or HAVING, is in the same unit as `unit`, so `IN CRORE` scales the displayed
      values and the thresholds alike.
    - Dates are filtered only by `period`, never in `filters`, so two date filters
      can't contradict each other.
    """

    metrics: tuple[Name, ...]
    dimensions: tuple[Name, ...] = ()
    filters: tuple[Condition, ...] = ()
    period: Period | None = None
    having: tuple[MetricCondition, ...] = ()
    order_by: Name | None = None  # explicit ORDER BY only
    order_dir: str = "DESC"  # "ASC" | "DESC"
    limit: int | None = None  # None means the user gave no LIMIT
    unit: str | None = None  # "CRORE" | "LAKH"
    chart_type: str | None = None  # from AS clause; never used to build SQL
    raw_dsl: str = ""  # the exact original DSL input string


@dataclass(frozen=True)
class ValidatedQuery:
    """The validator's output, and the ONLY type codegen accepts.

    Keeping this separate from QueryAST is deliberate: codegen is structurally unable
    to receive an unvalidated AST. Only the validator constructs this; nothing else
    should.

    - `ast` is the original query with its Names carrying resolved canonicals.
    - `joins` are deduplicated table names in a stable, deterministic order, so
      identical queries produce byte-identical SQL.
    - `order_by_kind` tells codegen how to reference the sort key in SQL.
    """

    ast: QueryAST
    joins: tuple[str, ...]
    order_by_kind: str | None = None  # "metric" | "dimension" | None


@dataclass(frozen=True)
class CompiledQuery:
    """Codegen's output.

    `assumptions` are keys (e.g. "success_default", "unit_crore", "period_anchor",
    "forced_limit") accumulated as validation/codegen rules fire. They are not known
    at parse time, which is why they live here and not on QueryAST.
    """

    sql: str
    dsl: str  # the original DSL string, for the audit panel
    chart_type: str | None  # carried through from the AS clause
    assumptions: tuple[str, ...]
