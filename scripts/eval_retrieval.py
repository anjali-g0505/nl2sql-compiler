"""Measure retrieval against a labelled question set, with and without reranking.

Each question names the document that should answer it. A hit means that document
appears in the top k. Reranking is applied to the fused shortlist, which is the usual
pattern: cheap retrieval proposes, an expensive cross-encoder reorders.

    python scripts\\eval_retrieval.py
    python scripts\\eval_retrieval.py --rerank-model Xenova/ms-marco-MiniLM-L-12-v2
    python scripts\\eval_retrieval.py --show-misses

Nothing here calls an LLM; this measures the search alone.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.index import Passage, Retriever  # noqa: E402

SHORTLIST = 8          # candidates the reranker reorders
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"

# question -> the document(s) that genuinely answer it. Where two documents both do,
# both count: the point is measuring retrieval, not enforcing one right answer.
QUESTIONS: List[Tuple[str, Set[str]]] = [
    ("what does do not honor mean", {"kb-03"}),
    ("what does a high 92 error code mean", {"kb-03"}),
    ("which codes mean the issuer was unreachable", {"kb-03"}),
    ("is insufficient funds worth retrying", {"kb-04", "kb-03"}),
    ("what is the difference between a technical and a business decline", {"kb-04"}),
    ("what is a soft decline", {"kb-04"}),
    ("how is average ticket size calculated", {"kb-11", "kb-05"}),
    ("what does spend per card mean", {"kb-11"}),
    ("which metrics count only successful transactions", {"kb-05", "kb-11"}),
    ("what is the difference between decline rate and business decline rate", {"kb-11", "kb-05"}),
    ("what is the active card rate denominator", {"kb-11"}),
    ("what tables are there and how do they join", {"kb-06"}),
    ("can I see a customer's name or phone number", {"kb-06", "kb-15"}),
    ("does the data link a retry to the original attempt", {"kb-06", "kb-10"}),
    ("how do I investigate a high decline rate for one issuer", {"kb-07"}),
    ("what does a decline profile dominated by 91 and 96 indicate", {"kb-07", "kb-03"}),
    ("how do I measure merchant concentration", {"kb-08"}),
    ("why do rankings by volume and by value disagree", {"kb-08"}),
    ("why does ats not equal value divided by volume", {"kb-09"}),
    ("what does a zero in the value column mean", {"kb-09", "kb-10"}),
    ("why can't I trust a rate computed on a small group", {"kb-10"}),
    ("what is a mix effect", {"kb-10"}),
    ("what is IntentQL", {"kb-12"}),
    ("why doesn't the model write SQL directly", {"kb-12", "kb-14"}),
    ("what are the stages a question goes through", {"kb-13"}),
    ("what happens when the model returns something that does not parse", {"kb-13"}),
    ("is the DSL a standard language or invented here", {"kb-14"}),
    ("what can the DSL not express", {"kb-14"}),
    ("can the system modify data in the database", {"kb-15"}),
    ("what does the system do when a filter value is ambiguous", {"kb-15", "kb-13"}),
]


def rank_of_hit(passages: Sequence[Passage], expected: Set[str]) -> int:
    for position, passage in enumerate(passages, start=1):
        if passage.doc_id in expected:
            return position
    return 0


def score(results: Sequence[Tuple[Sequence[Passage], Set[str]]]) -> dict:
    ranks = [rank_of_hit(passages, expected) for passages, expected in results]
    found = [r for r in ranks if r]
    return {
        "recall@1": sum(1 for r in ranks if r == 1) / len(ranks),
        "recall@3": sum(1 for r in ranks if 0 < r <= 3) / len(ranks),
        "recall@5": sum(1 for r in ranks if 0 < r <= 5) / len(ranks),
        "mrr": sum(1 / r for r in found) / len(ranks),
        "misses": [i for i, r in enumerate(ranks) if not r],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rerank-model", default=RERANK_MODEL)
    parser.add_argument("--shortlist", type=int, default=SHORTLIST)
    parser.add_argument("--show-misses", action="store_true")
    parser.add_argument("--per-question", action="store_true")
    args = parser.parse_args()

    from fastembed.rerank.cross_encoder import TextCrossEncoder

    retriever = Retriever()
    reranker = TextCrossEncoder(model_name=args.rerank_model)

    plain, reranked = [], []
    plain_time = rerank_time = 0.0

    for question, expected in QUESTIONS:
        started = time.time()
        shortlist = retriever.search(question, k=args.shortlist)
        plain_time += time.time() - started
        plain.append((shortlist, expected))

        started = time.time()
        if shortlist:
            scores = list(reranker.rerank(question, [p.text for p in shortlist]))
            order = sorted(zip(scores, shortlist), key=lambda pair: -pair[0])
            reordered = [passage for _, passage in order]
        else:
            reordered = []
        rerank_time += time.time() - started
        reranked.append((reordered, expected))

        if args.per_question:
            before = rank_of_hit(shortlist, expected) or "-"
            after = rank_of_hit(reordered, expected) or "-"
            moved = "" if before == after else f"   {before} -> {after}"
            print(f"  {str(before):>2} {str(after):>2}  {question[:58]:60}{moved}")

    for name, results, seconds in (
        ("hybrid only", plain, plain_time),
        (f"+ rerank ({args.rerank_model.split('/')[-1]})", reranked, plain_time + rerank_time),
    ):
        s = score(results)
        print(f"\n{name}")
        print(f"  recall@1 {s['recall@1']:.0%}   recall@3 {s['recall@3']:.0%}   "
              f"recall@5 {s['recall@5']:.0%}   MRR {s['mrr']:.3f}")
        print(f"  {seconds / len(QUESTIONS) * 1000:.0f} ms per question")
        if args.show_misses and s["misses"]:
            for i in s["misses"]:
                print(f"    missed: {QUESTIONS[i][0]}  (wanted {'/'.join(sorted(QUESTIONS[i][1]))})")


if __name__ == "__main__":
    main()
