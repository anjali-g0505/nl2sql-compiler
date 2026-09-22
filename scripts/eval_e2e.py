"""Score the whole pipeline end to end: question in, rows out, against the real database.

Same question sets as scripts/eval_translator.py (seen = grammar.md E1-E19, held-out =
HELD_OUT there), but nothing is stubbed: each question goes through Pipeline.ask exactly
as POST /query does it, with the Groq model, the app's one retry, the value indexes
built from MySQL, codegen at the data's reference date, and execution. The expected DSL
runs through the same pipeline, and the two RESULTS are compared, so an answer counts
as correct when it returns the same rows, however its SQL is written.

When the pipeline asks a clarification question, the eval answers it like a user would:
with the option that the expected DSL uses. If no option matches, the question is
scored CLAR (the pipeline couldn't get to an answer without a real user).

Per question:
    ok      same rows as the expected DSL
    DIFF    ran, but returned different rows
    FAIL    rejected after the retry (or out of scope); the stage is printed
    CLAR    stopped at a clarification the eval couldn't answer
    ERROR   the model or the database failed
    ORACLE  the expected DSL itself didn't run: fix the question set, not the model

Rows are compared as a multiset, or in order when the expected DSL has ORDER BY.
Numbers are rounded to 4 decimals. No row cap is applied unless --limit is given, so a
cut can't make two equal answers look different.

Usage (needs MySQL up and GROQ_API_KEY in .env; slow on purpose for the free tier):

    .\\.venv\\Scripts\\python.exe scripts\\eval_e2e.py
    .\\.venv\\Scripts\\python.exe scripts\\eval_e2e.py --set held-out --model openai/gpt-oss-20b
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import DatabaseError, execute_query  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.translator import GroqTranslator  # noqa: E402
from compiler.indexes import IndexRegistry  # noqa: E402
from compiler.parser import parse  # noqa: E402
from scripts.eval_translator import held_out, seen  # noqa: E402

QUOTED = re.compile(r"'([^']*)'")


def normalize(value):
    if isinstance(value, (Decimal, float)):
        return round(float(value), 4)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def result_key(rows, ordered):
    """(columns, rows) in a form two equal answers compare equal in."""
    columns = sorted(rows[0]) if rows else []
    values = [tuple(normalize(row[c]) for c in columns) for row in rows]
    return columns, values if ordered else sorted(values, key=repr)


def answer_clarifications(pipeline, outcome, expected_dsl):
    """Answer with the options the expected DSL uses; None if one can't be answered."""
    wanted = {v.lower() for v in QUOTED.findall(expected_dsl)}
    while outcome.body.get("status") == "needs_clarification":
        answers = {}
        for question in outcome.body["questions"]:
            match = next((o["id"] for o in question["options"]
                          if o["id"].lower() in wanted or o["label"].lower() in wanted), None)
            if match is None:
                return None
            answers[question["id"]] = match
        outcome = pipeline.answer(outcome.body["clarification_id"], answers)
    return outcome


def run_set(pipeline, examples, delay, pause_first):
    counts = dict(ran=0, first_try=0, clarified=0, correct=0, same_sql=0)
    seconds = []
    for position, (name, question, expected_dsl) in enumerate(examples):
        if position or pause_first:
            time.sleep(delay)
        try:
            expected = pipeline.ask(dsl=expected_dsl)
        except DatabaseError as exc:
            expected = None
            print(f"{name:4} ORACLE {question}\n       database: {exc}")
        if expected is not None and expected.body["status"] != "ok":
            expected = answer_clarifications(pipeline, expected, expected_dsl)
            if expected is None or expected.body["status"] != "ok":
                print(f"{name:4} ORACLE {question}\n       expected DSL does not run: {expected_dsl}")
                expected = None
        if expected is None:
            continue

        started = time.time()
        try:
            outcome = pipeline.ask(question=question)
            asked = outcome.body.get("status") == "needs_clarification"
            if asked:
                outcome = answer_clarifications(pipeline, outcome, expected_dsl)
        except DatabaseError as exc:
            print(f"{name:4} ERROR  {question}\n       database: {exc}")
            continue
        seconds.append(time.time() - started)

        if outcome is None:
            print(f"{name:4} CLAR   {question}\n       expected: {expected_dsl}")
            continue
        body = outcome.body
        if body["status"] != "ok":
            mark = "ERROR " if body.get("stage") == "translate" else "FAIL  "
            print(f"{name:4} {mark} {question}\n       {body.get('stage')}: {'; '.join(body['errors'])}")
            if body.get("dsl"):
                print(f"       got:      {body['dsl']}")
            print(f"       expected: {expected_dsl}")
            continue

        ordered = parse(expected_dsl).order is not None
        ok = result_key(body["rows"], ordered) == result_key(expected.body["rows"], ordered)
        counts["ran"] += 1
        counts["first_try"] += body["attempts"] == 1
        counts["clarified"] += asked
        counts["correct"] += ok
        counts["same_sql"] += body["sql"] == expected.body["sql"]

        mark = "ok    " if ok else "DIFF  "
        print(f"{name:4} {mark} {question}\n       got:      {body['dsl']}"
              + ("   (after a clarification)" if asked else ""))
        if not ok:
            print(f"       expected: {expected_dsl}\n"
                  f"       rows:     {body['row_count']} vs {expected.body['row_count']} expected")
    median = sorted(seconds)[len(seconds) // 2] if seconds else 0.0
    return len(examples), counts, median


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=settings.groq_model)
    parser.add_argument("--delay", type=float, default=12.0, help="seconds between questions")
    parser.add_argument("--set", choices=["all", "seen", "held-out"], default="all")
    parser.add_argument("--limit", type=int, default=0, help="row cap (0 = none)")
    args = parser.parse_args()
    if not settings.groq_api_key:
        sys.exit("GROQ_API_KEY is not set in .env")

    registry = IndexRegistry()
    report = registry.refresh()
    if report.failures:
        sys.exit("could not build the value indexes: "
                 + "; ".join(f"{s.name}: {s.error}" for s in report.failures))
    translator = GroqTranslator(
        settings.groq_api_key, args.model, fallback_model=None,
        reference_date=lambda: registry.reference_date,
    )
    pipeline = Pipeline(registry=registry, execute=execute_query, translator=translator,
                        forced_limit=args.limit)

    sets = {"seen": seen(), "held-out": held_out()}
    chosen = list(sets) if args.set == "all" else [args.set]
    print(f"model: {args.model}\nreference date: {registry.reference_date}\n")

    summary = []
    for number, set_name in enumerate(chosen):
        print(f"--- {set_name} ---")
        summary.append((set_name, *run_set(pipeline, sets[set_name], args.delay, number > 0)))

    print()
    for set_name, total, c, median in summary:
        print(f"{set_name:9} correct rows {c['correct']}/{total}, ran {c['ran']}/{total} "
              f"(first try {c['first_try']}, via clarification {c['clarified']}), "
              f"same SQL {c['same_sql']}/{total}, median {median:.1f}s")


if __name__ == "__main__":
    main()
