"""NL -> DSL with an LLM on Groq. The only place a model is called.

The model writes DSL, never SQL: whatever it returns still goes through the lexer,
parser, validator, resolver and codegen, so a bad answer is rejected (and retried once
by app/pipeline.py), not executed.

The prompt is built from config.yaml and grammar.md at startup, so it can't drift from
what the compiler accepts:
  * the syntax (grammar.md section 1)
  * the vocabulary: every metric, dimension (with aliases and enum values), attribute,
    period, unit and chart type in config
  * the rules for turning English into DSL (money units, periods, success, top-N)
  * the worked examples (question -> DSL) from grammar.md section 5
About 2K tokens, which matters on Groq's free tier (8K tokens/minute).

Groq's API is OpenAI-compatible; httpx is used directly rather than an SDK.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

from app.pipeline import Feedback, TranslatorError, Unanswerable
from compiler.semantic_layer import REPO_ROOT, SemanticLayer

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_FALLBACK_MODEL = "openai/gpt-oss-20b"  # its own rate limit; used on a 429
GRAMMAR_PATH = REPO_ROOT / "grammar.md"
CANNOT = "CANNOT:"  # the model's way of saying the data can't answer this

RULES = """\
Rules:
1. Reply with exactly ONE DSL query on one line and nothing else: no SQL, no explanation,
   no code fences. Clause order is fixed as in the grammar.
2. Use only the names listed under Vocabulary. Text values go in single quotes. For a
   dimension with listed values, use one of those values exactly.
3. For names of issuers, acquirers, merchants, categories, response codes, countries and
   cities, write the value as the user wrote it; it is matched against the database.
4. Money numbers are written in the IN unit: "more than 3 crore" -> `> 3 ... IN CRORE`;
   an amount in plain rupees -> no IN clause. "in lakh"/"in crore" -> IN LAKH / IN CRORE.
5. Relative time: today -> FTD, this week -> WTD, this month -> MTD, this quarter -> QTD,
   this year -> YTD, "last N days" -> LAST N DAYS, "last N months" -> LAST N MONTHS. Named months or dates ->
   FROM 'YYYY-MM-DD' TO 'YYYY-MM-DD' (a whole month: its first to last day). A date with
   no year uses {year}. No time mentioned -> no PERIOD clause. "Monthly", "per month",
   "month-wise" and "trend" mean BY month (a breakdown), not MTD.
6. value/ats/spend metrics already count only successful transactions. For "successful"
   counts write WHERE status = 'Success'; for declines use status or response.
7. A condition on each transaction's amount -> WHERE amount <op> n. A condition on a
   total per group -> HAVING <metric> <op> n (needs BY, and the metric must be in SHOW).
   {rates} are fractions between 0 and 1, so percentages are divided by 100:
   "under 10%" -> < 0.1, "above 85.5%" -> > 0.855.
8. "Top N" / "highest" -> ORDER BY <metric> DESC LIMIT N; "lowest" -> ASC.
9. Add AS <chart> only if the user asks for a chart type.
10. Customers are masked ids; names, phone numbers and addresses are not available.
11. If the question cannot be answered with this vocabulary, reply exactly:
    CANNOT: <one short reason>"""


def build_system_prompt(
    layer: Optional[SemanticLayer] = None,
    grammar_text: Optional[str] = None,
    year: Optional[int] = None,
) -> str:
    """The instructions, grammar, vocabulary and examples, from config and grammar.md."""
    layer = layer or SemanticLayer.load()
    grammar_text = grammar_text if grammar_text is not None else GRAMMAR_PATH.read_text("utf-8")
    parts = [
        "You translate questions about card-payment transactions into a small query "
        "language (DSL). A compiler turns the DSL into SQL.",
        "Grammar:\n" + _syntax(grammar_text),
        "Vocabulary:\n" + _vocabulary(layer),
        RULES.format(year=year or "the latest year in the data", rates=_fraction_metrics(layer)),
        "Examples:\n" + _examples(grammar_text),
    ]
    return "\n\n".join(parts)


def _syntax(grammar_text: str) -> str:
    """The EBNF block from grammar.md section 1, without the config-lookup lines."""
    section = grammar_text.split("## 1.", 1)[1].split("## 2.", 1)[0]
    block = section.split("```", 2)[1].strip("\n")
    return block.split("\n\nmetric      :=", 1)[0]


def _vocabulary(layer: SemanticLayer) -> str: #The vocabulary from config.yaml: every metric, dimension (with aliases and enum values), attribute, period, unit and chart type.
    #including lists of small set of fixed values for enums (card_type, card_variant, etc)
    lines = ["Metrics (SHOW, HAVING, ORDER BY):"]
    for key, entry in layer.metrics.items():
        lines.append(f"  {key}: {entry.get('label', key)}")
    lines.append("Dimensions (BY, text WHERE):")
    for key, entry in layer.dimensions.items():
        line = f"  {key}: {entry.get('label', key)}"
        if entry.get("aliases"):
            line += f"; also written {', '.join(entry['aliases'])}"
        if entry.get("values"):
            line += f"; values: {', '.join(repr(v) for v in entry['values'])}"
        lines.append(line)
    lines.append("Attributes (numeric WHERE only):")
    for key, entry in layer.attributes.items():
        lines.append(f"  {key}: {entry.get('label', key)}")
    lines.append(f"Periods: {', '.join(layer.period_names)}, LAST n DAYS, FROM 'date' TO 'date'")
    lines.append(f"Units: {', '.join(layer.units)}")
    lines.append(f"Charts: {', '.join(layer.chart_types)}")
    return "\n".join(lines)


def _fraction_metrics(layer: SemanticLayer) -> str:
    """The metrics config marks as fractions (range [0, 1]), e.g. the rates."""
    keys = [k for k in layer.metrics if layer.metric_range(k) == (0, 1)]
    return ", ".join(keys) if keys else "Rates"


def _examples(grammar_text: str) -> str:
    """Question -> DSL pairs from grammar.md's worked examples."""
    pairs = re.findall(r"\*\*E\d+ [^*]*\*\* \*(.*?)\*\n```\nDSL:\s+(.*?)\n```", grammar_text)
    return "\n".join(f"Q: {question}\nDSL: {dsl}" for question, dsl in pairs)


def clean_reply(text: str) -> str:
    """The DSL line from a model reply: drops code fences, a 'DSL:' prefix, newlines."""
    text = re.sub(r"```[a-zA-Z]*", "", text or "").strip()
    text = re.sub(r"^\s*DSL:\s*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


class GroqTranslator:
    """Translator (see app/pipeline.py) backed by a Groq-hosted model."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        fallback_model: Optional[str] = DEFAULT_FALLBACK_MODEL,
        layer: Optional[SemanticLayer] = None,
        reference_date: Callable[[], Optional[date]] = lambda: None,
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model if fallback_model != model else None
        self.layer = layer or SemanticLayer.load()
        self.reference_date = reference_date
        self.client = client or httpx.Client(timeout=timeout)
        self._grammar = GRAMMAR_PATH.read_text("utf-8")

    def translate(self, question: str, feedback: Optional[Feedback] = None) -> str:
        messages = self._messages(question, feedback)
        try:
            reply = self._complete(self.model, messages)
        except _RateLimited:
            if not self.fallback_model:
                raise TranslatorError("The language model is rate limited; try again in a minute.")
            try:
                reply = self._complete(self.fallback_model, messages)
            except _RateLimited:
                raise TranslatorError(
                    "The language model is rate limited; try again in a minute."
                ) from None

        dsl = clean_reply(reply)
        if dsl.upper().startswith(CANNOT):
            raise Unanswerable(dsl[len(CANNOT):].strip() or "This question can't be answered from the data.")
        if not dsl:
            raise TranslatorError("The language model returned an empty answer.")
        return dsl

    def _messages(self, question: str, feedback: Optional[Feedback]) -> List[Dict[str, str]]:
        reference = self.reference_date()
        system = build_system_prompt(self.layer, self._grammar, reference.year if reference else None)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]
        if feedback:  # the one retry: show the model its answer and what was wrong with it
            errors = "\n".join(f"- {e}" for e in feedback.errors)
            messages += [
                {"role": "assistant", "content": feedback.dsl},
                {"role": "user", "content": (
                    f"The compiler rejected that DSL:\n{errors}\n"
                    "Reply with the corrected DSL only."
                )},
            ]
        return messages

    def _complete(self, model: str, messages: List[Dict[str, str]]) -> str:
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": 1024,  # reasoning tokens count here too
        }
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"   # a short formal answer needs little thinking
            body["include_reasoning"] = False  # don't send it back: we only use the DSL
        try:
            response = self.client.post(
                GROQ_URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        except httpx.TimeoutException:
            raise TranslatorError("The language model timed out.") from None
        except httpx.HTTPError as exc:
            raise TranslatorError(f"Could not reach the language model: {type(exc).__name__}") from None

        if response.status_code == 429:
            raise _RateLimited()
        if response.status_code != 200:
            raise TranslatorError(f"The language model returned HTTP {response.status_code}: {_error_message(response)}")
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise TranslatorError("The language model returned an unexpected response.") from None


class _RateLimited(Exception):
    pass


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return response.text[:200]
