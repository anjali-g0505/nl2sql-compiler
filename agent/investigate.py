"""The investigation branch: plan once, execute in code, synthesize once.

Two model calls per investigation, whatever the number of steps:

    plan        (120b) question + grammar/vocabulary + playbook  -> JSON, at most 5 steps
    execute     (code) each step through pipeline.ask(dsl=...)   -> 0 tokens
    synthesize  (120b) question + evidence + playbook            -> the answer

~5.5K tokens in total, inside the 8K/min budget; a model call per step would be ~21K.
The model never writes SQL and never sees the database: every step is DSL, compiled and
guarded by the same pipeline as any other query.

Helpers here are pure functions; the nodes in agent/nodes.py call them.
"""
from __future__ import annotations

import json
import re
from statistics import mean
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from compiler.lexer import LexError, TokenType, tokenize
from compiler.resolver import normalize
from compiler.semantic_layer import SemanticLayer

MAX_STEPS = 5
MAX_NGRAM = 5                  # longest entity name looked for, in words
MIN_NGRAM_CHARS = 3            # "in", "of" are never entity names worth matching
EVIDENCE_ROWS = 15             # rows of each step shown to the synthesizer
OUTLIER_TOLERANCE = 0.10       # within 10% of the peer average is "not an outlier"
PLAYBOOK_SCOPES = ("reasoning playbook",)
PLAYBOOK_K = 2
PLAN_MAX_TOKENS = 900          # ~300 of JSON plus reasoning
SYNTH_MAX_TOKENS = 900
PLAN_OUTPUT_ESTIMATE = 500     # what a plan reply usually costs (budget estimate)
SYNTH_ESTIMATE = 2200          # a synthesis call, in and out (budget estimate)

PLANNER_INSTRUCTIONS = """\
PLANNING MODE. The rules above say how to write each DSL query; here you write up to \
five of them, as a plan to answer a "why" question. Ignore rule 1's one-query format \
and rule 11's CANNOT format: reply in the JSON format below instead.

Method. Choose at most 5 steps, in this order, skipping any that do not apply:
1. Confirm: the metric for the entity beside its peers, e.g. SHOW <metric> BY <the \
entity's dimension>. Always the first step.
2. Baseline: the overall figure (no BY), or the entity's own trend BY month.
3. Decompose: the metric's components or sibling metrics, e.g. decline_rate -> \
business_decline_rate, technical_decline_rate; value -> volume, ats.
4. Locate: break the metric down by one other dimension, filtered to the entity, \
largest first with a LIMIT of 5 to 10 (e.g. BY response, merchant, acquirer, mcc).
5. Size: the volume or value at stake.

Rules:
- Each "dsl" is ONE query in the grammar above, using only names from the Vocabulary.
- Clause order is fixed: SHOW, BY, WHERE, PERIOD, HAVING, ORDER BY, LIMIT, IN, AS. \
PERIOD always comes before ORDER BY and LIMIT.
- Investigate the metric the question names: "business decline rate" means \
business_decline_rate, "success rate" means success_rate. The playbook shows the \
method with its own example metric; do not copy that metric.
- When the question is about one class of decline, keep every breakdown to that class: \
for business declines WHERE status = 'Business Decline', for technical declines WHERE \
status = 'Technical Decline'. status != 'Success' mixes both classes.
- Write entity values exactly as listed under "Entities" when they are given.
- Add a PERIOD only if the question names a time ("this month", "in May", "last 90 \
days"), and then use that same period in every step. The playbook's periods are \
examples, not defaults: with no time in the question, write no PERIOD at all.
- Rates are fractions between 0 and 1.

Reply with JSON only, no prose and no code fences:
{"steps": [{"purpose": "<what this step establishes>", "dsl": "<one DSL query>"}]}
If the question cannot be investigated with this vocabulary, reply:
{"steps": [], "reason": "<one short reason>"}"""

SYNTH_SYSTEM = """\
You answer a payments analyst's "why" question from an investigation IntentQL has just \
run: a list of queries, each with its purpose, its DSL and the rows it returned.

Rules:
1. EVERY figure you state must appear in the evidence. Rates are already given as \
percentages; quote them as written. Lines marked "Computed:" give the entity's rank, \
the peer average and the gap: use them for every comparison ("higher", "lower", \
"ranks") instead of comparing figures yourself. A share ("x% of") needs its total in \
the evidence: rows cut by LIMIT are not a total, so give counts, not shares, for them.
2. First say whether the figure is actually unusual, using the comparison in the \
evidence. If it is not, say so plainly; that is the answer.
3. Do not assert causes: the data records outcomes, not reasons. Say where the \
difference sits (a response code, a merchant, a month) and what each code means, in \
business terms, using the documentation. Never write that the problem "lies with", \
is "due to" or is "caused by" policies, risk rules, systems or customers. Attribute a \
code to the class of decline its step was filtered to; if a step mixed classes, say so \
rather than assign its codes to one.
4. Respect the assumptions recorded with each step, e.g. "only successful transactions".
5. If a step failed or was not run, do not guess its result; name the query that would \
answer it.
6. Lead with the answer in one or two sentences, then at most four short points. Cite \
documentation you used as [kb-07 > How to phrase the conclusion]."""

HIGH_WORDS = re.compile(
    r"\b(high|higher|highest|so many|too many|spiked?|surged?|jumped|increased?|rising|rose)\b", re.I)
LOW_WORDS = re.compile(
    r"\b(low|lower|lowest|so few|too few|dropped|dipped|fell|falling|plunged?|decreased?)\b", re.I)


# --- entities -------------------------------------------------------------------

def find_entities(message: str, layer: SemanticLayer,
                  indexes: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Issuers, acquirers and merchants named in the message, matched exactly.

    Uses the resolver's own indexes (names, ids and aliases.yaml) on every phrase of up
    to five words, longest first, so "HDFC's" finds issuer 'HDFC Bank' without a model
    call. Only exact matches count: a guess here would steer the whole plan.
    """
    words = normalize(message).split()
    found: List[Dict[str, str]] = []
    seen = set()
    for field, entry in layer.dimensions.items():
        if entry.get("resolution") != "entity":
            continue
        index = indexes.get(entry.get("entity"))
        if index is None:
            continue
        for n in range(min(MAX_NGRAM, len(words)), 0, -1):
            for start in range(len(words) - n + 1):
                phrase = " ".join(words[start:start + n])
                if len(phrase) < MIN_NGRAM_CHARS:
                    continue
                candidate = index.lookup_exact(phrase)
                if candidate and (field, candidate.name) not in seen:
                    seen.add((field, candidate.name))
                    found.append({"field": field, "value": candidate.name, "matched": phrase})
    return found


MIXED_DECLINES = re.compile(r"\bstatus\s*!=\s*'Success'", re.I)
DECLINE_CLASSES = {"business_decline": "Business Decline", "technical_decline": "Technical Decline"}


def narrow_decline_class(steps: List[Dict[str, str]], focus: Optional[Mapping[str, str]]) -> Optional[str]:
    """Keep breakdowns to the class of decline the question is about.

    Asked about business declines, the planner still wrote `status != 'Success'` (all
    declines) in two of four real runs, and the answer then counted technical codes
    such as 91 towards the business rate. When the focus metric names a class, that
    filter is narrowed to it here. Returns a note when it changed anything.
    """
    metric = (focus or {}).get("metric", "")
    wanted = next((name for prefix, name in DECLINE_CLASSES.items() if metric.startswith(prefix)), None)
    if not wanted:
        return None
    changed = 0
    for step in steps:
        narrowed = MIXED_DECLINES.sub(f"status = '{wanted}'", step["dsl"])
        if narrowed != step["dsl"]:
            step["dsl"], changed = narrowed, changed + 1
    return f"{changed} step(s) narrowed from all declines to '{wanted}'." if changed else None


def find_metric(message: str, layer: SemanticLayer) -> Optional[Dict[str, str]]:
    """The metric the question names, by its key or label, longest match first.

    "business decline rate" -> business_decline_rate, not decline_rate. None when no
    metric is named; the planner then picks one from the question as usual.
    """
    text = f" {normalize(message)} "
    best: Optional[Tuple[int, str]] = None
    for key, entry in layer.metrics.items():
        label = re.sub(r"\(.*?\)", "", str(entry.get("label", "")))
        for phrase in {normalize(key.replace("_", " ")), normalize(label)} - {""}:
            if f" {phrase} " in text and (best is None or len(phrase) > best[0]):
                best = (len(phrase), key)
    if best is None:
        return None
    key = best[1]
    return {"metric": key, "label": str(layer.metric(key).get("label", key))}


# --- planning -------------------------------------------------------------------

def planner_user_prompt(question: str, entities: Sequence[Mapping[str, str]],
                        reference_date: Optional[str], playbook: str,
                        focus: Optional[Mapping[str, str]] = None) -> str:
    named = "\n".join(f"- {e['field']} = '{e['value']}'" for e in entities) or "- none recognised"
    metric = (f"Metric the question asks about: {focus['metric']} ({focus['label']}). Steps 1 "
              "and 2 use exactly this metric; decompose it with its siblings.\n\n") if focus else ""
    return (
        f"Question: {question}\n\n"
        f"{metric}"
        f"Entities:\n{named}\n\n"
        f"Latest date in the data: {reference_date or 'unknown'}\n\n"
        + (f"Playbook:\n\n{playbook}" if playbook else "No playbook passage matched; use the method.")
    )


def parse_plan(reply: str) -> Dict[str, Any]:
    """{"steps": [...at most MAX_STEPS...], "reason": str|None, "dropped": int}.

    Lenient about wrapping (code fences, prose around the JSON), strict about content:
    a step without a DSL string is dropped. More than MAX_STEPS steps are cut here, in
    code, whatever the prompt said.
    """
    text = re.sub(r"```[a-zA-Z]*", "", reply or "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {"steps": [], "reason": "the planner did not return a plan", "dropped": 0}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"steps": [], "reason": "the planner returned malformed JSON", "dropped": 0}

    steps = []
    for raw in data.get("steps") or []:
        if isinstance(raw, dict) and isinstance(raw.get("dsl"), str) and raw["dsl"].strip():
            steps.append({"purpose": str(raw.get("purpose") or "").strip(),
                          "dsl": " ".join(raw["dsl"].split())})
    return {
        "steps": steps[:MAX_STEPS],
        "reason": data.get("reason") if not steps else None,
        "dropped": max(0, len(steps) - MAX_STEPS),
    }


# --- executing ------------------------------------------------------------------

# the grammar's fixed clause order; ORDER covers "ORDER BY", IN the unit clause
CLAUSE_ORDER = ("SHOW", "BY", "WHERE", "PERIOD", "HAVING", "ORDER", "LIMIT", "IN", "AS")
_CLAUSE_KEYWORDS = {TokenType.SHOW, TokenType.WHERE, TokenType.PERIOD, TokenType.HAVING,
                    TokenType.ORDER, TokenType.LIMIT, TokenType.AS}


def split_clauses(dsl: str) -> Optional[List[tuple]]:
    """[(clause, text)] in written order, or None if the query doesn't lex or starts
    oddly. ORDER covers "ORDER BY"; IN is the unit clause, not an IN (...) list."""
    try:
        tokens = tokenize(dsl)
    except LexError:
        return None
    starts: List[tuple] = []          # (clause, character offset)
    depth = 0
    for i, token in enumerate(tokens):
        kind = token.type
        if kind is TokenType.LPAREN:
            depth += 1
        elif kind is TokenType.RPAREN:
            depth -= 1
        if depth or kind in (TokenType.LPAREN, TokenType.RPAREN):
            continue
        before = tokens[i - 1].type if i else None
        after = tokens[i + 1].type if i + 1 < len(tokens) else None
        if kind in _CLAUSE_KEYWORDS:
            starts.append((kind.name, token.position))
        elif kind is TokenType.BY and before is not TokenType.ORDER:
            starts.append(("BY", token.position))
        elif kind is TokenType.IN and after is TokenType.IDENTIFIER:
            starts.append(("IN", token.position))
    if not starts or starts[0][1] != 0:
        return None
    bounds = [pos for _, pos in starts] + [len(dsl)]
    return [(name, dsl[bounds[i]:bounds[i + 1]].strip()) for i, (name, _) in enumerate(starts)]


def reorder_clauses(dsl: str) -> str:
    """The same query with its clauses in grammar order, e.g. LIMIT 10 PERIOD YTD ->
    PERIOD YTD ... LIMIT 10.

    Clause order carries no meaning, so putting it right is a deterministic fix that
    needs no model call: a step the planner wrote out of order still runs. Only order
    is touched. A query that doesn't lex, or repeats a clause, is returned as it was
    and the compiler reports the real problem.
    """
    clauses = split_clauses(dsl)
    if not clauses or len({name for name, _ in clauses}) != len(clauses):
        return dsl
    clauses.sort(key=lambda clause: CLAUSE_ORDER.index(clause[0]))
    return " ".join(text for _, text in clauses)


def ensure_confirm_step(steps: List[Dict[str, str]], focus: Optional[Mapping[str, str]],
                        entities: Sequence[Mapping[str, str]], layer: SemanticLayer) -> Optional[str]:
    """Make step 1 compare the metric the question asked about, beside the entity's peers.

    In real runs the planner sometimes copied the playbook's example metric
    (decline_rate) when the question named business_decline_rate, and every comparison
    after that answered a different question. Step 1 is the one the whole answer rests
    on, so when it doesn't use the focus metric it is rebuilt here, keeping the period
    the planner chose. Returns a note when it did so.
    """
    if not focus or not entities or not steps:
        return None
    metric, field = focus["metric"], entities[0]["field"]
    if re.search(rf"\b{re.escape(metric)}\b", steps[0]["dsl"]):
        return None
    period = next((text for name, text in split_clauses(steps[0]["dsl"]) or [] if name == "PERIOD"), "")
    label = layer.metric(metric).get("label", metric)
    steps[0] = {
        "purpose": f"Confirm: {label} for {entities[0]['value']} beside its peers",
        "dsl": f"SHOW {metric} BY {field}" + (f" {period}" if period else ""),
    }
    return f"Step 1 was rebuilt to compare {metric}, the metric the question asks about."


def evidence_from(step: Mapping[str, str], body: Mapping[str, Any]) -> Dict[str, Any]:
    """One step's result, trimmed to what the synthesizer and the UI need."""
    status = body.get("status")
    record: Dict[str, Any] = {"purpose": step.get("purpose", ""), "dsl": step["dsl"], "status": status}
    if status == "ok":
        rows = body.get("rows") or []
        record.update({
            "rows": rows[:EVIDENCE_ROWS],
            "row_count": body.get("row_count", len(rows)),
            "columns": body.get("columns", []),
            "metric_columns": body.get("metric_columns", []),
            "dimension_columns": body.get("dimension_columns", []),
            "labels": body.get("labels", {}),
            "chart_type": body.get("chart_type"),
            "unit": body.get("unit"),
            "sql": body.get("sql"),
            "assumptions": [a.get("text") for a in body.get("assumptions") or []],
        })
    elif status == "needs_clarification":
        record["errors"] = [q.get("message", "") for q in body.get("questions") or []]
    else:
        record["errors"] = list(body.get("errors") or [])
    return record


def direction(message: str) -> Optional[str]:
    """"high" or "low" if the question says which way the figure is unusual."""
    high, low = bool(HIGH_WORDS.search(message)), bool(LOW_WORDS.search(message))
    return "high" if high and not low else "low" if low and not high else None


def outlier_check(first: Mapping[str, Any], entities: Sequence[Mapping[str, str]],
                  way: Optional[str]) -> Optional[str]:
    """A note if step 1 shows the entity is NOT unusual against its peers, else None.

    The most common honest answer to "why is X so high" is "it isn't". This decides it
    in code, from the confirm step's rows, so the remaining steps aren't spent proving
    the wrong premise. Only fires on a clean shape: one metric, one dimension, the
    entity present, at least one peer.
    """
    if not way or first.get("status") != "ok":
        return None
    metrics, dims = first.get("metric_columns") or [], first.get("dimension_columns") or []
    if len(metrics) != 1 or len(dims) != 1:
        return None
    metric, dim = metrics[0], dims[0]
    names = {e["value"] for e in entities}
    target = [r for r in first.get("rows", []) if str(r.get(dim)) in names]
    peers = [r for r in first.get("rows", []) if str(r.get(dim)) not in names and r.get(metric) is not None]
    if len(target) != 1 or not peers or target[0].get(metric) is None:
        return None

    value, average = float(target[0][metric]), mean(float(r[metric]) for r in peers)
    unusual = (value > average * (1 + OUTLIER_TOLERANCE) if way == "high"
               else value < average * (1 - OUTLIER_TOLERANCE))
    if unusual:
        return None
    label = (first.get("labels") or {}).get(metric, metric)
    return (f"{target[0][dim]}'s {label} ({value:.4g}) is not {way}er than its peers' average "
            f"({average:.4g}, within {OUTLIER_TOLERANCE:.0%}), so the premise does not hold; "
            "the remaining steps were not run.")


# --- synthesizing ---------------------------------------------------------------

def as_percentages(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Rates written as percentages ("9.08%") instead of fractions (0.0908).

    Done in code because the model, given 0.08 and told rates are fractions, still
    wrote "0.08 %" in a real run. A figure it doesn't have to convert can't be
    converted wrongly. Same rule as the UI's: a column ending in _rate is a fraction.
    """
    def show(column: str, value: Any) -> Any:
        if column.endswith("_rate") and isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{value * 100:.2f}%"
        return value
    return [{column: show(column, value) for column, value in row.items()} for row in rows]


def _figure(column: str, value: float, difference: bool = False) -> str:
    if column.endswith("_rate"):
        return f"{value * 100:.2f} percentage points" if difference else f"{value * 100:.2f}%"
    return f"{value:,.2f}" if abs(value) >= 100 else f"{value:.4g}"


def entity_comparisons(step: Mapping[str, Any], names: Sequence[str]) -> List[str]:
    """Where the entity stands in a step's rows: rank, peer average, the gap.

    Computed in code because the model, comparing figures itself, wrote "3.168 lakh,
    higher than SBI (3.392 lakh)" in a real run. Only for a complete result with one
    dimension, the entity in it, and at least one peer.
    """
    rows = step.get("rows") or []
    dims, metrics = step.get("dimension_columns") or [], step.get("metric_columns") or []
    if len(dims) != 1 or len(rows) < 2 or step.get("row_count", len(rows)) != len(rows):
        return []
    dim = dims[0]
    target = [r for r in rows if str(r.get(dim)) in names]
    if len(target) != 1:
        return []
    who, lines = target[0][dim], []
    for metric in metrics:
        mine = target[0].get(metric)
        values = [float(r[metric]) for r in rows if isinstance(r.get(metric), (int, float))]
        peers = [float(r[metric]) for r in rows
                 if str(r.get(dim)) not in names and isinstance(r.get(metric), (int, float))]
        if not isinstance(mine, (int, float)) or not peers:
            continue
        mine, average = float(mine), mean(peers)
        rank = 1 + sum(v > mine for v in values)
        side = "above" if mine > average else "below" if mine < average else "level with"
        lines.append(
            f"{who}: {metric} {_figure(metric, mine)}, rank {rank} of {len(values)} (1 = highest); "
            f"peer average {_figure(metric, average)}; {side} it by "
            f"{_figure(metric, abs(mine - average), difference=True)}")
    return lines


def render_evidence(evidence: Sequence[Mapping[str, Any]],
                    entities: Sequence[Mapping[str, str]] = ()) -> str:
    """The steps as the synthesizer sees them: purpose, DSL, rows, and the entity's
    computed standing. No SQL (saves ~30%)."""
    names = [e["value"] for e in entities]
    blocks = []
    for number, step in enumerate(evidence, 1):
        lines = [f"Step {number}: {step.get('purpose') or '(no purpose given)'}",
                 f"DSL: {step['dsl']}"]
        if step.get("status") == "ok":
            shown = as_percentages(step.get("rows") or [])
            more = step.get("row_count", len(shown)) - len(shown)
            lines.append("Rows: " + json.dumps(shown, default=str, ensure_ascii=False)
                         + (f" (and {more} more)" if more > 0 else ""))
            for comparison in entity_comparisons(step, names):
                lines.append("Computed: " + comparison)
            if step.get("assumptions"):
                lines.append("Assumptions: " + "; ".join(step["assumptions"]))
            if step.get("unit"):
                lines.append(f"Money figures in {str(step['unit']).lower()}.")
        else:
            lines.append(f"Not answered ({step.get('status')}): " + "; ".join(step.get("errors") or []))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def synth_user_prompt(question: str, evidence: Sequence[Mapping[str, Any]],
                      stop_note: Optional[str], playbook: str,
                      entities: Sequence[Mapping[str, str]] = ()) -> str:
    parts = [f"Question: {question}", "Evidence:\n\n" + render_evidence(evidence, entities)]
    if stop_note:
        parts.append(f"Stopped early: {stop_note}")
    parts.append(f"Documentation:\n\n{playbook}" if playbook
                 else "No documentation passage was relevant.")
    return "\n\n".join(parts)
