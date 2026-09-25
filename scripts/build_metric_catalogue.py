"""Generate knowledge/11_metric_catalogue.md from config.yaml.

The catalogue answers "what does this metric mean and how is it calculated". It is
generated because config.yaml is the single source of truth for metrics: a metric added
or changed there must not leave a stale prose description behind. Nothing here reads the
database — the catalogue describes definitions, never figures.

    .\\.venv\\Scripts\\python.exe scripts\\build_metric_catalogue.py

The interpretation notes below are hand-written per metric; everything else (names,
labels, formulas, joins, ranges) comes from config.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.semantic_layer import SemanticLayer  # noqa: E402

OUTPUT = Path(__file__).resolve().parent.parent / "knowledge" / "11_metric_catalogue.md"

# What a reader needs beyond the formula: when to reach for it, and how it misleads.
NOTES = {
    "volume": "The plain count of attempts, declines included. Use it for demand and reliability "
              "questions. It is not a money figure, and 'high volume' never means 'high revenue'.",
    "value": "The revenue figure. Declined attempts carry an amount but moved no money, so they are "
             "excluded; a sum that includes them overstates every total.",
    "ats": "Average ticket size: how large a typical successful transaction is. Undefined, not zero, "
           "for a group with no successful transactions.",
    "spend_per_card": "Value spread over the cards that transacted. Falls when a campaign brings in "
                      "many lightly-used cards, which is not the same as customers spending less.",
    "spend_per_customer": "Value spread over customers who transacted. Compare with spend per card to "
                          "see whether people hold several active cards.",
    "active_cards": "Cards that attempted at least one transaction in the period. Activity, not issuance.",
    "active_card_rate": "Active cards against every card ever issued, so the denominator includes cards "
                        "that have never been used and does not shrink with the period. A short period "
                        "therefore looks low by construction.",
    "success_rate": "The share of attempts approved. The headline health measure; its complement is the "
                    "decline rate.",
    "decline_rate": "Every non-approval: business and technical together. This is what 'decline rate' "
                    "means unqualified, and it can rank groups differently from either class alone.",
    "business_decline_rate": "Only issuer refusals about the account. Customer affordability, limits and "
                             "risk policy; largely outside the payment organization's control.",
    "technical_decline_rate": "Only infrastructure failures. Usually actionable and usually recoverable, "
                              "so a rise here is an incident rather than a trend.",
    "success_value": "Value stated explicitly rather than implicitly, useful beside the declined columns.",
    "success_volume": "Approved attempts only, for comparison with the declined counts.",
    "success_ats": "Ticket size of approved transactions.",
    "decline_value": "Money attempted but not captured. The size of the problem, which a rate never says.",
    "decline_volume": "All failed attempts, business and technical.",
    "decline_ats": "Ticket size of failed attempts. Compare with success_ats: if declines are larger, "
                   "failures are concentrated in high-ticket transactions and cost more than the rate suggests.",
    "business_decline_value": "Money lost to issuer refusals.",
    "business_decline_volume": "Count of issuer refusals.",
    "business_decline_ats": "Ticket size of issuer refusals; a high figure points at limit codes.",
    "technical_decline_value": "Money lost to system failures — the recoverable part of lost revenue.",
    "technical_decline_volume": "Count of system failures.",
    "technical_decline_ats": "Ticket size of system failures. Usually close to the overall ticket size, "
                             "because infrastructure does not care how large a transaction is.",
}

AGG_WORDS = {"SUM": "sum of", "COUNT": "count of", "COUNT_DISTINCT": "distinct count of"}


def measure_phrase(layer: SemanticLayer, name: str) -> str:
    spec = layer.measures[name]
    what = "transactions" if spec["expr"] == "*" else f"`{spec['expr']}`"
    phrase = f"{AGG_WORDS[spec['agg']]} {what}"
    if spec.get("filter"):
        phrase += f" where `{spec['filter']}`"
    return phrase


def formula(layer: SemanticLayer, key: str) -> str:
    entry = layer.metrics[key]
    if "sql" in entry:
        return f"`{entry['sql']}`"
    if "measure" in entry:
        return measure_phrase(layer, entry["measure"]).capitalize()
    numerator, denominator = entry["ratio"]
    return f"{measure_phrase(layer, numerator).capitalize()} ÷ {measure_phrase(layer, denominator)}"


def counts(layer: SemanticLayer, key: str) -> str:
    """Which population the metric measures, in one phrase."""
    filters = {
        layer.measures[m].get("filter")
        for m in layer.metric_measures(key)
        if layer.measures.get(m, {}).get("filter")
    }
    if layer.metrics[key].get("default_success_filter"):
        return "successful transactions (applied automatically unless the query names an outcome)"
    if not filters:
        return "every attempt, whatever its outcome"
    return " and ".join(sorted(f"transactions where `{f}`" for f in filters))


def main() -> None:
    layer = SemanticLayer.load()
    lines = [
        "---",
        "doc_id: kb-11",
        "title: Metric Catalogue — what each metric means and how it is calculated",
        "scope: definitions",
        "topic: metrics",
        "confidence: high (generated from config.yaml, the compiler's source of truth)",
        "source: generated by scripts/build_metric_catalogue.py — do not edit by hand",
        f"last_updated: {date.today().isoformat()}",
        "---",
        "",
        "# Metric catalogue",
        "",
        "Every metric this system can compute, with its formula and what it counts. These are the only "
        "names a query may use; anything else is rejected rather than approximated. No figures appear "
        "here — a metric's value is whatever the query returns today.",
        "",
    ]
    for key, entry in layer.metrics.items():
        bounds = layer.metric_range(key)
        lines += [
            f"## `{key}` — {entry.get('label', key)}",
            "",
            f"- **Calculation:** {formula(layer, key)}",
            f"- **Counts:** {counts(layer, key)}",
        ]
        if bounds:
            lines.append(f"- **Range:** {bounds[0]} to {bounds[1]} — a fraction, so 10% is written 0.1")
        if entry.get("unit_scalable"):
            lines.append("- **Units:** money, so `IN LAKH` or `IN CRORE` rescales the displayed figure")
        joins = list(layer.requires_join(key, "metric"))
        if joins:
            lines.append(f"- **Needs:** the {', '.join(joins)} join")
        if entry.get("note"):
            lines.append(f"- **Note:** {entry['note']}")
        if key in NOTES:
            lines += ["", NOTES[key]]
        lines.append("")

    lines += [
        "## Choosing between them",
        "",
        "- **Money or count?** `value` and its variants are rupees; `volume` and its variants are counts. "
        "A question about revenue takes the first, a question about attempts or reliability the second.",
        "- **Which outcome?** An unqualified money or count metric already means successful transactions. "
        "Name an outcome explicitly (`success_*`, `business_decline_*`, `technical_decline_*`, `decline_*`) "
        "when outcomes need to sit side by side in one table.",
        "- **Rate or amount?** A rate says how often something happens; a value says what it is worth. "
        "Questions about impact need the amount, because a small rate on large transactions can cost more "
        "than a large rate on small ones.",
        "- **Rates are fractions.** Every metric with a range of 0 to 1 is written that way in a query: "
        "'under 10%' is `< 0.1`.",
        "",
    ]
    text = "\n".join(lines)
    with open(OUTPUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"wrote {OUTPUT.name}: {len(layer.metrics)} metrics, {len(text.split())} words")


if __name__ == "__main__":
    main()
