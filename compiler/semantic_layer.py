"""Loads config.yaml — the vocabulary every stage after the parser binds to.

The lexer and parser know DSL syntax; everything else (which metrics exist, what a
dimension selects, which joins it needs, which units and chart types are legal) lives
in config.yaml and is read here, once. The validator, the value resolver and codegen
all take a SemanticLayer rather than re-reading YAML or hardcoding names.

    layer = SemanticLayer.load()
    layer.resolve("category", "dimension")   -> "mcc"       (alias, case-insensitive)
    layer.dimension("mcc")["filter_column"]  -> "m.mcc_code"
    layer.order_joins({"response_master", "issuer_master"})
        -> ("issuer_master", "response_master")             (config declaration order)

Names are matched case-insensitively (a small closed vocabulary: `Value`, `VALUE` and
`value` can only mean one thing). Filter VALUES are never touched here — they are
database data, where case matters, and the resolver handles them.

load() self-checks the file, so a typo in config.yaml fails at startup with a clear
message instead of producing wrong SQL later.

there are two types of aliases - named aliases (defined in config.yaml) and aliases defined in the aliases.yaml file
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

import yaml
from rapidfuzz import process

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"

# kind -> the config section holding it
SECTIONS = {"metric": "metrics", "dimension": "dimensions", "attribute": "attributes"}
SUGGESTION_THRESHOLD = 75  # below this, "did you mean" is noise rather than help
MEASURE_AGGS = ("SUM", "COUNT", "COUNT_DISTINCT")  # what codegen knows how to render
METRIC_SHAPES = ("measure", "ratio", "sql")  # a metric is built exactly one of these ways
#sections variable is a dictionary that maps the kind of entity (metric, dimension, attribute) to the corresponding section in the config.yaml file. 
#suggestion_threshold variable is an integer that defines the minimum score for suggesting a similar name when a user inputs an unknown name. If the score is below this threshold, the suggestion will not be made as it is considered noise

class ConfigError(Exception):
    """Raised when config.yaml itself is inconsistent (not when a query is wrong)."""


@dataclass(frozen=True)
class SemanticLayer:
    """A parsed, checked config.yaml with the lookups the compiler stages need."""

    raw: Mapping  # the whole document, for sections not modelled here
    metrics: Mapping[str, Mapping]
    dimensions: Mapping[str, Mapping]
    attributes: Mapping[str, Mapping]
    measures: Mapping[str, Mapping]  # base aggregates that metrics are built from
    joins: Mapping[str, Mapping]  # declaration order == emitted join order
    units: Mapping[str, int]  # CRORE -> 10000000
    chart_types: Tuple[str, ...]
    period_names: Tuple[str, ...]  # FTD, WTD, ... (LAST/FROM are syntax, not names)
    guardrails: Mapping
    assumptions: Mapping[str, str]
    _aliases: Mapping[str, Mapping[str, str]] = field(repr=False, default_factory=dict) #Mapping is a dictionary that maps the kind of entity (metric, dimension, attribute) to another dictionary that maps the lowercased alias or key to the canonical key. This is used for resolving aliases and keys. 

    # --- loading --------------------------------------------------------------

    @staticmethod
    def load(path: Optional[Path] = None) -> "SemanticLayer":
        """Load and check config.yaml (cached per path)."""
        return _load_cached(str(path or CONFIG_PATH))

    @staticmethod
    def from_dict(document: Mapping) -> "SemanticLayer":
        """Build from an already-loaded document — handy for tests with a small config."""
        #used when we want to create a SemanticLayer from a dictionary instead of loading it from a file. This is useful for testing purposes, where we can define a small config in the test code
        modifiers = document.get("modifiers", {})
        unit_config = modifiers.get("unit", {})
        units = {
            name.upper(): value
            for name, value in unit_config.items()
            if isinstance(value, int)
        }
        specs = modifiers.get("period", {}).get("specs", [])
        period_names = tuple(str(s).upper() for s in specs if str(s).isalpha())

        layer = SemanticLayer(
            raw=document,
            metrics=document.get("metrics", {}),
            dimensions=document.get("dimensions", {}),
            attributes=document.get("attributes", {}),
            measures=document.get("measures", {}),
            joins=document.get("joins", {}),
            units=units,
            chart_types=tuple(modifiers.get("chart", {}).get("types", [])),
            period_names=period_names,
            guardrails=document.get("guardrails", {}),
            assumptions=document.get("assumptions", {}),
            _aliases=_build_aliases(document),
        )
        layer.check()
        return layer

    def check(self) -> None:
        """Fail fast on an inconsistent config, before any query reaches it."""
        problems = []
        alias_owners: Dict[str, str] = {}
        #this function is called when the SemanticLayer is created to validate the configuration and ensure that it is consistent. It checks for issues such as missing joins, alias clashes, and other inconsistencies in the configuration. If any problems are found, it raises a ConfigError with a message describing the issues.
        for kind, section in SECTIONS.items():
            for key, entry in getattr(self, section).items():
                for join in entry.get("requires_join", []):
                    if join not in self.joins:
                        problems.append(f"{kind} {key!r} requires unknown join {join!r}")
                for alias in [key, *entry.get("aliases", [])]:
                    owner = f"{kind}:{key}"
                    clash = alias_owners.get(alias.lower())
                    if clash and clash != owner:
                        problems.append(f"alias {alias!r} is claimed by {clash} and {owner}")
                    alias_owners[alias.lower()] = owner

        problems += self._check_metric_shapes()

        for key, entry in self.dimensions.items():
            select = entry.get("select", "")
            if len(split_columns(select)) > 1 and not entry.get("filter_column"):
                problems.append(
                    f"dimension {key!r} selects several columns and needs a filter_column"
                )
            if entry.get("resolution") == "enum" and not entry.get("values"):
                problems.append(f"dimension {key!r} is resolution: enum but lists no values")

        for rule in self.raw.get("semantics", {}).values():
            emitted = rule.get("emits_assumption", [])
            for key in [emitted] if isinstance(emitted, str) else emitted:
                if key not in self.assumptions:
                    problems.append(f"semantics emits unknown assumption {key!r}")

        if problems:
            raise ConfigError("config.yaml is inconsistent:\n  - " + "\n  - ".join(problems))

    def _check_metric_shapes(self) -> list:
        """Every metric is built one way, from measures that exist and can be rendered."""
        problems = []
        for key, entry in self.measures.items():
            if entry.get("agg") not in MEASURE_AGGS:
                problems.append(
                    f"measure {key!r} has agg {entry.get('agg')!r}; "
                    f"expected one of: {', '.join(MEASURE_AGGS)}"
                )
            if not entry.get("expr"):
                problems.append(f"measure {key!r} has no expr")
            for join in entry.get("requires_join", []):
                if join not in self.joins:
                    problems.append(f"measure {key!r} requires unknown join {join!r}")

        for key, entry in self.metrics.items():
            shapes = [s for s in METRIC_SHAPES if s in entry]
            if len(shapes) != 1:
                problems.append(
                    f"metric {key!r} needs exactly one of: {', '.join(METRIC_SHAPES)}"
                )
                continue
            if "ratio" in entry and len(entry["ratio"]) != 2:
                problems.append(f"metric {key!r} ratio needs [numerator, denominator]")
                continue
            for measure in self.metric_measures(key):
                if measure not in self.measures:
                    problems.append(f"metric {key!r} uses unknown measure {measure!r}")
            if "sql" in entry and entry.get("default_success_filter"):
                problems.append(
                    f"metric {key!r} is raw sql, so the success filter can't be pushed "
                    f"into it; build it from measures instead"
                )
            emitted = entry.get("emits_assumption", [])
            for assumption in [emitted] if isinstance(emitted, str) else emitted:
                if assumption not in self.assumptions:
                    problems.append(f"metric {key!r} emits unknown assumption {assumption!r}")

        if any(m.get("default_success_filter") for m in self.metrics.values()):
            if not self.success_condition:
                problems.append(
                    "semantics.implicit_success needs a condition: some metric "
                    "defaults to the success filter"
                )
        return problems

    # --- metrics and measures -------------------------------------------------

    def metric_measures(self, key: str) -> Tuple[str, ...]:
        """The measures a metric is built from: one, two (a ratio), or none (raw sql)."""
        entry = self.metrics[key]
        if "measure" in entry:
            return (entry["measure"],)
        return tuple(entry.get("ratio", ()))

    def filter_column(self, key: str) -> Optional[str]:
        """The one column a WHERE on this dimension compares, or None if there isn't one.

        filter_column wins when set (SUBSTRING(t.date,1,7) is one column despite its
        commas); otherwise the select, if it is a single column.
        """
        entry = self.dimensions[key]
        if entry.get("filter_column"):
            return entry["filter_column"]
        columns = split_columns(entry.get("select", ""))
        return columns[0] if len(columns) == 1 else None

    def sql_fragments(self, key: str, kind: str) -> Tuple[str, ...]:
        """Every SQL fragment an entry can put into a query, for guardrail checks."""
        entry = self.entry(key, kind)
        fragments = [entry.get(k) for k in ("select", "filter_column", "column", "sql")]
        if kind == "metric":
            for measure in self.metric_measures(key):
                spec = self.measures.get(measure, {})
                fragments += [spec.get("expr"), spec.get("filter")]
        return tuple(str(f) for f in fragments if f)

    @property
    def success_condition(self) -> Optional[str]:
        """The implicit success filter's SQL condition, e.g. r.TD_BD = 'Success'."""
        return self.raw.get("semantics", {}).get("implicit_success", {}).get("condition")

    @property
    def success_joins(self) -> Sequence[str]:
        """Joins the success condition needs."""
        rule = self.raw.get("semantics", {}).get("implicit_success", {})
        return rule.get("requires_join", [])

    # --- lookups --------------------------------------------------------------

    def resolve(self, name: str, kind: str) -> Optional[str]:
        """An alias or key (any case) -> the canonical config key, or None."""
        return self._aliases.get(kind, {}).get(name.lower()) #the two .get are used to safely access the nested dictionaries. The first .get retrieves the dictionary of aliases for the specified kind (metric, dimension, or attribute). If the kind is not found, it returns an empty dictionary. The second .get retrieves the canonical key for the given name (case-insensitive) from the aliases dictionary. If the name is not found, it returns None. This allows for a safe lookup of canonical keys without raising KeyError exceptions.

    def kind_of(self, name: str) -> Optional[str]:
        """Which section a name belongs to: 'metric', 'dimension' or 'attribute'."""
        for kind in SECTIONS:
            if self.resolve(name, kind):
                return kind
        return None

    def metric(self, key: str) -> Mapping:
        return self.metrics[key]

    def dimension(self, key: str) -> Mapping:
        return self.dimensions[key]

    def attribute(self, key: str) -> Mapping:
        return self.attributes[key]

    def entry(self, key: str, kind: str) -> Mapping:
        return getattr(self, SECTIONS[kind])[key]

    def requires_join(self, key: str, kind: str) -> Sequence[str]:
        """The entry's own joins, plus (for a metric) those of its measures."""
        joins = list(self.entry(key, kind).get("requires_join", []))
        if kind == "metric":
            for measure in self.metric_measures(key):
                joins += self.measures.get(measure, {}).get("requires_join", [])
        return joins

    def order_joins(self, names: Iterable[str]) -> Tuple[str, ...]:
        """Deduplicate join names into config declaration order.

        Deterministic on purpose: the same query written two ways must produce
        byte-identical SQL, so join order can't depend on clause order.
        """
        #for input like: {"response_master", "issuer_master"}, it returns a tuple of join names in the order they are declared in the config.yaml file. This ensures that the generated SQL is consistent and deterministic, regardless of the order in which the joins were specified in the query.
        wanted = set(names)
        return tuple(name for name in self.joins if name in wanted)

    def suggest(self, name: str, kind: str) -> Optional[str]: #uses rapidfuzz to find the closest matching name for a given input name and kind (metric, dimension, or attribute). It returns the canonical key of the closest match if the similarity score is above the SUGGESTION_THRESHOLD; otherwise, it returns None. This is useful for providing "did you mean ..." suggestions in error messages when a user inputs an unknown name.
        """The closest known name, for 'did you mean ...' in an error message."""
        candidates = list(self._aliases.get(kind, {})) 
        if not candidates:
            return None
        match = process.extractOne(name.lower(), candidates, score_cutoff=SUGGESTION_THRESHOLD)
        return self._aliases[kind][match[0]] if match else None

    def known(self, kind: str) -> Tuple[str, ...]:
        """Canonical keys of a kind, for listing in error messages."""
        return tuple(getattr(self, SECTIONS[kind]))


def split_columns(fragment: str) -> Tuple[str, ...]:
    """Split a SQL select list on its top-level commas only.

    "m.mcc_code, m.mcc_description" -> two columns, but "SUBSTRING(t.date,1,7)" is
    one: its commas are inside parentheses.
    """
    parts, depth, current = [], 0, ""
    for char in fragment or "":
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return tuple(parts)


def _build_aliases(document: Mapping) -> Dict[str, Dict[str, str]]:
    """{kind: {lowercased alias or key: canonical key}} for every section."""
    aliases: Dict[str, Dict[str, str]] = {kind: {} for kind in SECTIONS}
    for kind, section in SECTIONS.items():
        for key, entry in document.get(section, {}).items():
            aliases[kind][key.lower()] = key
            for alias in entry.get("aliases", []):
                aliases[kind][str(alias).lower()] = key
    return aliases


@lru_cache(maxsize=4)
def _load_cached(path: str) -> SemanticLayer:
    with open(path, encoding="utf-8") as handle:
        return SemanticLayer.from_dict(yaml.safe_load(handle))
