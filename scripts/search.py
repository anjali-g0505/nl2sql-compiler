"""Search the knowledge index from the command line — no LLM involved.

This is where chunking and retrieval are judged: type real questions and read what
comes back before any of it reaches a model.

    python scripts\\search.py "what does do not honor mean"
    python scripts\\search.py "how is ats calculated" --k 3 --full
    python scripts\\search.py "what is intentql" --scope product architecture
    python scripts\\search.py --questions questions.txt      # one per line, for a sweep
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.index import Retriever, StaleIndexError  # noqa: E402


def show(retriever: Retriever, question: str, k: int, scope, full: bool) -> None:
    passages = retriever.search(question, k=k, scope=scope)
    print(f"\n> {question}")
    if not passages:
        print("   (nothing above the score floor — the corpus has no answer for this)")
        return
    for rank, passage in enumerate(passages, start=1):
        print(f"  {rank}. {passage.score:.4f}  {passage.citation}")
        print(f"      scope={passage.scope} | matched child {passage.matched_child}")
        body = passage.text.split("\n\n", 1)[-1].strip()
        print("      " + (body if full else body[:220].replace("\n", " ") + ("…" if len(body) > 220 else "")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*", help="the question to search for")
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--scope", nargs="*", help="filter by document scope")
    parser.add_argument("--full", action="store_true", help="print whole passages")
    parser.add_argument("--questions", help="file of questions, one per line")
    args = parser.parse_args()

    try:
        retriever = Retriever()
    except StaleIndexError as exc:
        sys.exit(str(exc))

    questions = []
    if args.question:
        questions.append(" ".join(args.question))
    if args.questions:
        questions += [line.strip() for line in Path(args.questions).read_text("utf-8").splitlines() if line.strip()]
    if not questions:
        sys.exit("give a question, or --questions with a file of them")

    for question in questions:
        show(retriever, question, args.k, args.scope, args.full)


if __name__ == "__main__":
    main()
