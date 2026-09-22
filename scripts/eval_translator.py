"""Score a Groq model on NL -> DSL questions.

Two sets, reported separately:
  * seen      the grammar.md questions (E1-E20). They are also the prompt's examples,
              so this mostly checks that the model copies them faithfully.
  * held-out  new questions below, not in the prompt: the honest accuracy number.

For each question the model's DSL is compiled and compared with the expected DSL's SQL,
so an answer written differently but meaning the same thing (an alias, a different
spelling of the same clause) still counts. Reports:

    compiled first try   the first answer passed parse + validate + value checks
    after retry          it needed the one retry to compile
    correct              its SQL equals the expected SQL (after the retry, if any)

Usage (reads GROQ_API_KEY from .env; runs slowly on purpose, to stay under the free
tier's tokens-per-minute limit):

    .\\.venv\\Scripts\\python.exe scripts\\eval_translator.py
    .\\.venv\\Scripts\\python.exe scripts\\eval_translator.py --set held-out --model openai/gpt-oss-20b
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.pipeline import Feedback, TranslatorError, Unanswerable  # noqa: E402
from app.translator import GRAMMAR_PATH, GroqTranslator  # noqa: E402
from compiler.codegen import generate  # noqa: E402
from compiler.parser import LexError, ParseError, parse  # noqa: E402
from compiler.resolver import resolve  # noqa: E402
from compiler.validator import ValidationError, validate  # noqa: E402

REFERENCE_DATE = date(2025, 12, 27)  # the date grammar.md's examples assume
ORACLE = re.compile(r'\*\*(E\d+) [^*]*\*\* \*"?(.*?)"?\*\n```\nDSL:\s+(.*?)\n```')

# Not in the prompt. Add a line whenever a real question goes wrong.
HELD_OUT = [
    ("Total volume of transactions", "SHOW volume"),
    ("What is the average ticket size by card type?", "SHOW ats BY card_type"),
    ("Value of debit card transactions in the last 7 days, in lakh",
     "SHOW value WHERE card_type = 'Debit' PERIOD LAST 7 DAYS IN LAKH"),
    ("Which 10 merchants had the most transactions this year?",
     "SHOW volume BY merchant PERIOD YTD ORDER BY volume DESC LIMIT 10"),
    ("Monthly success rate", "SHOW success_rate BY month"),
    ("How many active cards were there in October?",
     "SHOW active_cards PERIOD FROM '2025-10-01' TO '2025-10-31'"),
    ("Technical decline rate by acquirer this quarter",
     "SHOW technical_decline_rate BY acquirer PERIOD QTD"),
    ("Value by country, excluding prepaid cards",
     "SHOW value BY country WHERE card_type != 'Prepaid'"),
    ("Issuers with more than 50 lakh in value, shown in lakh",
     "SHOW value BY issuer HAVING value > 50 IN LAKH"),
    ("Number of platinum card transactions per city",
     "SHOW volume BY location WHERE card_variant = 'Platinum'"),
    ("Spend per customer by issuer as a bar chart",
     "SHOW spend_per_customer BY issuer AS BAR"),
    ("How many transactions under 500 rupees were there, by card type?",
     "SHOW volume BY card_type WHERE amount < 500"),
    # asked by the user before config had outcome-split metrics; see docs/verified-queries.md
    ("Value and volume of successful and business declined transactions per issuer",
     "SHOW success_value, business_decline_value, success_volume, business_decline_volume BY issuer"),
    ("Merchants under 10% business decline rate",
     "SHOW business_decline_rate BY merchant HAVING business_decline_rate < 0.1"),
    ("Value for every card type and variant over the past 5 months",
     "SHOW value BY card_type, card_variant PERIOD LAST 5 MONTHS"),
    # typos on purpose: the model should still pick the right enum values
    ("Successful volume and value per issuer for card type credt and card variant bussines",
     "SHOW success_volume, success_value BY issuer WHERE card_type = 'Credit' "
     "AND card_variant = 'Business'"),
]


def seen():
    return ORACLE.findall(GRAMMAR_PATH.read_text("utf-8"))


def held_out():
    return [(f"H{n}", question, dsl) for n, (question, dsl) in enumerate(HELD_OUT, 1)]


def compile_sql(dsl):
    """(sql, None) or (None, errors). Values aren't matched to the DB here."""
    try:
        resolved = resolve(validate(parse(dsl)), indexes={})
    except (LexError, ParseError) as exc:
        return None, [str(exc)]
    except ValidationError as exc:
        return None, list(exc.errors)
    if not resolved.ok:
        return None, [f"{r.field} has no value {r.raw!r}" for r in resolved.unresolved]
    return generate(resolved, REFERENCE_DATE).sql, None


def run_set(translator, examples, delay, pause_first):
    compiles = retried = correct = 0
    seconds = []
    for position, (name, question, expected_dsl) in enumerate(examples):
        if position or pause_first:
            time.sleep(delay)
        expected_sql, _ = compile_sql(expected_dsl)
        started = time.time()
        try:
            dsl = translator.translate(question)
            sql, errors = compile_sql(dsl)
            first_ok = sql is not None
            if not first_ok:
                dsl = translator.translate(question, Feedback(dsl=dsl, errors=tuple(errors)))
                sql, errors = compile_sql(dsl)
        except (TranslatorError, Unanswerable) as exc:
            print(f"{name:4} ERROR {question}\n       {type(exc).__name__}: {exc}")
            continue
        seconds.append(time.time() - started)

        compiles += first_ok
        retried += (not first_ok) and sql is not None
        ok = sql is not None and sql == expected_sql
        correct += ok
        mark = "ok   " if ok else ("DIFF " if sql else "FAIL ")
        print(f"{name:4} {mark} {question}\n       got:      {dsl}")
        if not ok:
            print(f"       expected: {expected_dsl}")
    median = sorted(seconds)[len(seconds) // 2] if seconds else 0.0
    return len(examples), compiles, retried, correct, median


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=settings.groq_model)
    parser.add_argument("--delay", type=float, default=12.0, help="seconds between questions")
    parser.add_argument("--set", choices=["all", "seen", "held-out"], default="all")
    args = parser.parse_args()
    if not settings.groq_api_key:
        sys.exit("GROQ_API_KEY is not set in .env")

    translator = GroqTranslator(
        settings.groq_api_key, args.model, fallback_model=None,
        reference_date=lambda: REFERENCE_DATE,
    )
    sets = {"seen": seen(), "held-out": held_out()}
    chosen = list(sets) if args.set == "all" else [args.set]
    print(f"model: {args.model}\n")

    summary = []
    for number, set_name in enumerate(chosen):
        print(f"--- {set_name} ---")
        summary.append((set_name, *run_set(translator, sets[set_name], args.delay, number > 0)))

    print()
    for set_name, total, compiles, retried, correct, median in summary:
        print(f"{set_name:9} compiled first try {compiles}/{total}, after retry +{retried}, "
              f"correct {correct}/{total}, median {median:.1f}s")


if __name__ == "__main__":
    main()
