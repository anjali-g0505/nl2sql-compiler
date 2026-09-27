"""Answers that come from the documentation, and explanations of a result.

Two jobs, one rule: **the model may only use what it is given.**

    knowledge("what does do not honor mean")     retrieved passages, no figures
    explain(result, "why is this high?")         the result itself, plus passages

The difference matters. A knowledge answer has no data in front of it, so it must not
state a figure at all — the corpus deliberately contains none, and a number invented to
sound complete is exactly the failure this system exists to avoid. An explanation does
have data: the rows, the DSL, the SQL and the assumptions that produced them, and every
figure it uses must appear there.

Both cite the passages they used, so an answer can be checked the same way the SQL can.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.groq import GroqChat, ModelError, messages
from rag.index import Passage, Retriever

MAX_PASSAGES = 4
MAX_ROWS_SHOWN = 40         # beyond this, the head plus the row count
KNOWLEDGE_SCOPES = None     # the whole corpus: product and method questions both land here
EXPLAIN_SCOPES = (
    "reasoning playbook", "interpretation aid", "guardrails", "definitions",
    "reference", "business logic", "schema",
)

NOT_IN_DOCS = (
    "That isn't covered in the IntentQL documentation. Ask about the payments data "
    "itself and I can query it, or ask about metrics, response codes or how the system "
    "works and I can answer from the documentation."
)

KNOWLEDGE_SYSTEM = """\
You answer questions about IntentQL, a system that answers questions about card-payment \
data, and about the payments domain it covers.

Rules:
1. Use ONLY the passages provided. If they do not answer the question, say so plainly.
2. State NO figures — no counts, rates, shares or rankings. The documentation contains \
none, and the data is only available by running a query. If a question needs a number, \
say which query would produce it.
3. Cite the passages you used by their id, in square brackets, at the end of the \
sentence they support: [kb-03 > Reading the classes].
4. Be direct and brief: two to five sentences unless the question needs a list.
5. If a passage is marked as medium confidence, say that the point should be confirmed.
6. Never invent a metric, a dimension, a response code or a capability that the passages \
do not mention."""

EXPLAIN_SYSTEM = """\
You explain a result that IntentQL has just computed, to a payments analyst.

You are given the question, the DSL it compiled to, the SQL that ran, the rows returned \
and the assumptions the compiler recorded. You are also given passages from the \
documentation that say how to read results of this kind.

Rules:
1. EVERY figure you state must appear in the rows given to you. Do not recall numbers \
from anywhere else. Simple arithmetic on those rows is fine — a share, a difference, a \
ratio — and nothing more.
2. The assumptions bound what you may say: if they state the figure counts only \
successful transactions, do not describe it as all attempts.
3. A comparison you were not given requires another query. Say which query would answer \
it rather than guessing whether a number is high or low.
4. Do not assert causes. The data records outcomes, not reasons. Name where a difference \
sits — a code, a group, a period — and stop there.
5. If only part of a large result was provided, say so, and describe it as the visible \
rows in the query's sort order.
6. Lead with the answer in one sentence, then at most three short points. Cite \
documentation you used as [kb-09 > Mixed-outcome tables]."""


@dataclass(frozen=True)
class Answer:
    """What the assistant says, and what it based it on."""

    text: str
    citations: List[str] = field(default_factory=list)
    passages: List[str] = field(default_factory=list)   # chunk ids, for debugging
    grounded: bool = True                                # False when nothing was retrieved

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": "answer",
            "text": self.text,
            "citations": self.citations,
            "passages": self.passages,
            "grounded": self.grounded,
        }


@dataclass
class Answerer:
    """Retrieval plus a model, with the grounding rules applied."""

    retriever: Retriever
    chat: GroqChat
    max_passages: int = MAX_PASSAGES

    # --- knowledge ------------------------------------------------------------

    def knowledge(self, question: str) -> Answer:
        passages = self.retriever.search(question, k=self.max_passages, scope=KNOWLEDGE_SCOPES)
        if not passages:
            return Answer(text=NOT_IN_DOCS, grounded=False)
        prompt = f"Question: {question}\n\nPassages:\n\n{render_passages(passages)}"
        return self._answer(KNOWLEDGE_SYSTEM, prompt, passages)

    # --- explanation ----------------------------------------------------------

    def explain(self, result: Dict[str, Any], question: Optional[str] = None) -> Answer:
        asked = question or "Explain this result."
        # the search is seeded with the original question and the metrics involved, so a
        # bare "explain this" still retrieves something relevant
        seed = " ".join(filter(None, [asked, result.get("question") or result.get("dsl", "")]))
        passages = self.retriever.search(seed, k=self.max_passages, scope=EXPLAIN_SCOPES)
        prompt = (
            f"{render_result(result)}\n\n"
            f"The analyst asks: {asked}\n\n"
            + (f"Documentation:\n\n{render_passages(passages)}" if passages else
               "No documentation passage was relevant; explain from the result alone.")
        )
        return self._answer(EXPLAIN_SYSTEM, prompt, passages)

    def _answer(self, system: str, prompt: str, passages: Sequence[Passage]) -> Answer:
        try:
            text = self.chat.complete(messages(system, prompt), max_tokens=700).strip()
        except ModelError as exc:
            raise exc
        # what the model actually cited, in order, without repeats; if it cited nothing
        # recognisable, list what it was given, so a reader can still check the source
        cited = [p.citation for p in passages if f"[{p.citation}]" in text]
        citations = list(dict.fromkeys(cited or [p.citation for p in passages]))
        return Answer(
            text=text,
            citations=citations,
            passages=list(dict.fromkeys(p.chunk_id for p in passages)),
        )


def render_passages(passages: Sequence[Passage]) -> str:
    """Passages as the model sees them: id, confidence, then the text."""
    blocks = []
    for passage in passages:
        confidence = passage.confidence.split("(")[0].strip().lower()
        marker = " (medium confidence)" if confidence.startswith("medium") else ""
        body = passage.text.split("\n\n", 1)[-1].strip()
        blocks.append(f"[{passage.citation}]{marker}\n{body}")
    return "\n\n---\n\n".join(blocks)


def render_result(result: Dict[str, Any]) -> str:
    """The result as evidence: what was asked, what ran, what came back."""
    rows = result.get("rows") or []
    shown = rows[:MAX_ROWS_SHOWN]
    labels = result.get("labels") or {}
    lines = [
        f"Question asked: {result.get('question') or '(sent as DSL)'}",
        f"DSL: {result.get('dsl', '')}",
        f"SQL:\n{result.get('sql', '')}",
        f"Rows returned: {result.get('row_count', len(rows))}"
        + (f" (the first {len(shown)} are shown below)" if len(rows) > len(shown) else ""),
        f"Columns: {', '.join(f'{c} ({labels.get(c, c)})' for c in result.get('columns', []))}",
        "Data:",
        json.dumps(shown, default=str, ensure_ascii=False),
    ]
    assumptions = result.get("assumptions") or []
    if assumptions:
        lines.append("Assumptions the compiler recorded:")
        lines += [f"- {a['text']}" for a in assumptions]
    if result.get("unit"):
        lines.append(f"Money figures are displayed in {result['unit'].lower()}.")
    return "\n".join(lines)
