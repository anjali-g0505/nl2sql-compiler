"""Routing: two votes with confidences, then hard rules the votes cannot override.

    regex_vote()    app/router.py's patterns plus an investigate rule. Each rule carries
                    a fixed confidence: an explicit "explain this" is near-certain, the
                    fall-through to data (no rule matched) is a guess.
    classify        gpt-oss-20b replies {"route", "confidence"}. Its own 8K/min quota.
    decide()        1. drop any vote the rules forbid
                    2. the higher confidence wins; a tie goes to the regex vote
                    3. a "why" question that didn't land on knowledge or explain goes to
                       investigate

The rules, enforced here in code whatever either vote says:

  * EXPLAIN only when the previous answer in this session was a data result (status
    ok, route data). After a knowledge answer, an investigation or an error there is
    nothing on screen to explain, so an explain vote is ignored.
  * A "why" question goes to INVESTIGATE unless it was routed to KNOWLEDGE (a why about
    the product or a definition) or to an allowed EXPLAIN.

Both votes, their confidences and the reason for the decision are kept in state, so
every routing decision can be audited from the checkpoint.

On the classifier's confidence: it is self-reported, not a probability, and models
tend to report high numbers. The regex confidences are set so that only a strong
pattern (explicit explain, diagnostic or definitional phrasing) can beat a confident
classifier, and the no-match default (0.5) almost never does.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.router import ASKS_TO_EXPLAIN, DEFINITIONAL, _is_bare_follow_up

DATA, KNOWLEDGE, EXPLAIN, INVESTIGATE = "data", "knowledge", "explain", "investigate"
ROUTES = (DATA, KNOWLEDGE, EXPLAIN, INVESTIGATE)

# "why" questions about the data, and the words that signal a diagnosis is wanted.
INVESTIGATE_RULE = re.compile(
    r"^\s*(why\b|how come\b|what (caused|causes|drove|drives|explains|is driving|is behind)\b)"
    r"|\b(so|too|unusually|abnormally|very) (high|low)\b"
    r"|\b(spiked?|dropped|dipped|surged?|plunged?|jumped)\b",
    re.I,
)
ABOUT_THE_SYSTEM = re.compile(r"\b(intentql|the system|the model|the compiler|the dsl|the app)\b", re.I)
WHY_QUESTION = re.compile(r"\b(why|how come)\b", re.I)

# how sure each regex rule is; see the module docstring
CONFIDENCE = {
    "explicit request to explain": 0.95,
    "short follow-up pointing at the result": 0.80,
    "diagnostic phrasing": 0.85,
    "definitional phrasing": 0.85,
    "no rule matched": 0.50,
}
DEFAULT_LLM_CONFIDENCE = 0.5   # a classifier reply without a usable confidence

CLASSIFIER_SYSTEM = """\
Classify a message sent to IntentQL, an assistant over card-payment transaction data \
(issuers, acquirers, merchants, response codes, declines, value, volume).

Routes:
data - wants figures, lists, rankings, trends or breakdowns read from the data. \
"top 5 merchants by value this month", "what is the decline rate for HDFC", \
"monthly volume by card type"
knowledge - wants a definition, the meaning of a code or metric, or how the system works; \
needs no figures. "what does do not honor mean", "what's the difference between a \
business and a technical decline", "how is ATS calculated", "can it make charts"
explain - asks about the data result shown just before, by pointing back at it \
("that", "it", "this result") without naming a new issuer, merchant, metric or concept. \
"why is that so high?", "what am I looking at", "explain this". Only possible when the \
previous answer was a data result.
investigate - asks WHY a figure is high, low or has changed, naming what it is about, \
or for a diagnosis that needs several queries. "why is HDFC's business decline rate so \
high this month", "why is HSBC's average ticket size so low compared to the other \
issuers", "what is driving the drop in SBI's success rate"

Reply with JSON only, no prose:
{"route": "data" | "knowledge" | "explain" | "investigate", "confidence": <0.0 to 1.0, \
how sure you are>}"""

CLASSIFIER_MAX_TOKENS = 200  # a short JSON object, plus low-effort reasoning
ROUTE_WORD = re.compile(r"\b(data|knowledge|explain|investigate)\b", re.I)


@dataclass(frozen=True)
class Vote:
    route: str
    confidence: float
    basis: str            # the regex rule that fired, or "classifier"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --- the two votes ----------------------------------------------------------------

def regex_vote(message: str, explainable: bool) -> Vote:
    """The rule-based vote, with the confidence of the rule that fired.

    Order: an explicit request to explain; a short follow-up pointing back (only when
    there is a data result to point at); diagnostic phrasing (unless it is about the
    product); definitional phrasing; otherwise data.
    """
    text = message.strip()
    if ASKS_TO_EXPLAIN.search(text):
        basis, route = "explicit request to explain", EXPLAIN
    elif explainable and _is_bare_follow_up(text):
        basis, route = "short follow-up pointing at the result", EXPLAIN
    elif INVESTIGATE_RULE.search(text) and not ABOUT_THE_SYSTEM.search(text):
        basis, route = "diagnostic phrasing", INVESTIGATE
    elif DEFINITIONAL.search(text):
        basis, route = "definitional phrasing", KNOWLEDGE
    else:
        basis, route = "no rule matched", DATA
    return Vote(route, CONFIDENCE[basis], basis)


def regex_route(message: str, explainable: bool) -> str:
    """Just the route of the regex vote."""
    return regex_vote(message, explainable).route


def classifier_messages(message: str, explainable: bool) -> List[Dict[str, str]]:
    previous = "yes" if explainable else "no"
    return [
        {"role": "system", "content": CLASSIFIER_SYSTEM},
        {"role": "user", "content": f"The previous answer was a data result: {previous}\nMessage: {message}"},
    ]


def parse_vote(reply: str) -> Optional[Vote]:
    """The classifier's vote from its reply, or None if it named no single route.

    JSON first; a bare route word is accepted with a neutral confidence, so a model
    that ignores the format still gets a (weak) say.
    """
    text = reply or ""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            route = str(data.get("route", "")).strip().lower()
            if route in ROUTES:
                return Vote(route, _confidence(data.get("confidence")), "classifier")
        except (json.JSONDecodeError, AttributeError):
            pass
    found = {m.lower() for m in ROUTE_WORD.findall(text)}
    return Vote(found.pop(), DEFAULT_LLM_CONFIDENCE, "classifier") if len(found) == 1 else None


def parse_route(reply: str) -> Optional[str]:
    vote = parse_vote(reply)
    return vote.route if vote else None


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_LLM_CONFIDENCE
    if 1 < number <= 100:      # "85" meaning 85%
        number /= 100
    return min(max(number, 0.0), 1.0)


# --- the decision -------------------------------------------------------------------

def decide(regex: Vote, llm: Optional[Vote], message: str, explainable: bool) -> Tuple[str, str]:
    """(route, reason). See the module docstring for the rules."""
    notes: List[str] = []
    allowed: List[Tuple[str, Vote]] = []
    for who, vote in (("regex", regex), ("classifier", llm)):
        if vote is None:
            notes.append(f"{who}: no vote")
            continue
        if vote.route == EXPLAIN and not explainable:
            notes.append(f"{who} explain ({vote.confidence:.2f}) ignored: "
                         "the previous answer was not a data result")
            continue
        allowed.append((who, vote))

    if allowed:
        # max() keeps the first of equals, and regex is first: a tie goes to regex
        who, winner = max(allowed, key=lambda item: item[1].confidence)
        route = winner.route
        notes.append(f"{who} {route} wins ({winner.confidence:.2f})" if len(allowed) > 1
                     else f"{who} {route} ({winner.confidence:.2f})")
    else:
        # both votes were an explain the rules forbid: the explain node will say there
        # is nothing to explain, unless the why rule below sends it to investigate
        route = EXPLAIN
        notes.append("nothing explainable")

    # a why that isn't about the docs or an explainable result is a diagnosis
    explained = route == EXPLAIN and explainable
    if WHY_QUESTION.search(message) and route != INVESTIGATE and route != KNOWLEDGE and not explained:
        notes.append(f"why-question rule: {route} -> investigate")
        route = INVESTIGATE
    return route, "; ".join(notes)
