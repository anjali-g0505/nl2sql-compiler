"""AST and pipeline types for the DSL defined in grammar.md.

These are the data shapes the stages bind to:

    parser     -> QueryAST        faithful transcript of the DSL, nothing inferred
    validator  -> ValidatedQuery  names resolved, joins computed
    resolver   -> ResolvedQuery   filter values matched to real data (compiler/resolver.py);
                                  wraps a ValidatedQuery and is codegen's only input
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
SORT_DIRECTIONS = frozenset({"ASC", "DESC"})
SUCCESS_FILTER_MODES = frozenset({"none", "where", "conditional"})


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
class Order:
    """The ORDER BY clause: sort key plus direction, held as ONE field on QueryAST.

    Keeping them together means a direction can't exist without a key: no ORDER BY is
    `order is None`, not a key of None beside a meaningless "DESC". The direction is
    optional in the DSL and defaults to DESC (config modifiers.top_n.default_direction),
    since ORDER BY is nearly always used for ranking.

    Default orderings for some dimensions (e.g. `month` chronologically) are applied
    later by codegen; only an explicit ORDER BY is recorded here.
    """

    key: Name  # a metric or dimension; which one is the validator's call
    direction: str = "DESC"  # "ASC" | "DESC"

    def __post_init__(self) -> None:
        if self.direction not in SORT_DIRECTIONS:
            raise ValueError(f"Unknown sort direction {self.direction!r}")


@dataclass(frozen=True)
class QueryAST: #this class is just to check the structure of the query and to make sure that the query is valid. It does not perform any validation or code generation.
    #so that when the object is created, it can be used to check the structure of the query and to make sure that the query is valid
    #used when the query is parsed and the AST is created.
    """The parser's output: a faithful transcript of what was written.

    The AST records intent; defaults and policy are applied downstream. In particular:

    - Collections are tuples, not lists, so the frozen dataclass is genuinely
      immutable (a frozen dataclass holding a list can still have the list mutated).
    - `limit` stays None when the user gave no LIMIT. Execution applies the
      forced-limit guardrail and emits the `forced_limit` assumption; the AST must
      preserve "user gave nothing" so that can happen.
    - `order` is None unless the query had an explicit ORDER BY clause.
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
    order: Order | None = None  # explicit ORDER BY only
    limit: int | None = None  # None means the user gave no LIMIT
    unit: str | None = None  # "CRORE" | "LAKH"
    chart_type: str | None = None  # from AS clause; never used to build SQL
    raw_dsl: str = ""  # the exact original DSL input string


@dataclass(frozen=True)
class ValidatedQuery:
    """The validator's output. Codegen accepts it only inside the resolver's
    ResolvedQuery, so it never sees unvalidated names or unresolved values.

    Keeping this separate from QueryAST is deliberate: codegen is structurally unable
    to receive an unvalidated AST. Only the validator constructs this; nothing else
    should.

    - `ast` is the original query with its Names carrying resolved canonicals.
    - `joins` are deduplicated table names in a stable, deterministic order, so
      identical queries produce byte-identical SQL.
    - `order_by_kind` tells codegen how to reference the sort key in SQL.
    - `success_filter` records the implicit-success decision, which the validator
      makes because `joins` depends on it: "where" adds WHERE r.TD_BD='Success',
      "conditional" moves it inside the money metrics (mixed metrics, see E14), and
      "none" means it was suppressed by an outcome dimension or never applied.
      Codegen emits what it is told here rather than re-deriving the rule.
    """

    ast: QueryAST
    joins: tuple[str, ...]
    order_by_kind: str | None = None  # "metric" | "dimension" | None
    success_filter: str = "none"  # "none" | "where" | "conditional"

    def __post_init__(self) -> None:
        if self.success_filter not in SUCCESS_FILTER_MODES:
            raise ValueError(f"Unknown success filter mode {self.success_filter!r}")


@dataclass(frozen=True)
class Assumption:
    """One assumption that fired: a key under config.yaml -> assumptions, plus the
    values its template needs, e.g. ("period_anchor", (("ref", "2025-12-27"),)).

    Params are a tuple of pairs, not a dict, so the type stays frozen and hashable.
    Keeping the values (not just rendered text) lets the audit panel and follow-up
    questions re-render or explain an assumption later.
    """

    key: str
    params: tuple[tuple[str, str], ...] = ()

    def render(self, template: str) -> str:
        return template.format(**dict(self.params))


@dataclass(frozen=True)
class CompiledQuery:
    """Codegen's output.

    Two forms of the same SQL:
    - `sql` has every value written in, quoted: what the audit panel shows and what
      the grammar.md oracle compares against. Never executed.
    - `executable_sql` has a `%s` placeholder per text value, with the values in
      `params`, so a value can never change the query's structure. This is the one
      the database runs.

    `assumptions` accumulate as codegen rules fire (success default, unit, period,
    corrected values, ...). They are not known at parse time, which is why they live
    here and not on QueryAST. `forced_limit` is added at execution, not here: the
    runaway-query cap is applied when rows are fetched and never appears in the SQL.
    """

    sql: str
    dsl: str  # the original DSL string, for the audit panel
    chart_type: str | None  # carried through from the AS clause
    assumptions: tuple[Assumption, ...]
    executable_sql: str = ""
    params: tuple[str, ...] = ()
