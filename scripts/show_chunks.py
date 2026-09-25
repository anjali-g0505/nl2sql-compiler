"""Inspect the chunking before anything is embedded.

Chunking is judged by reading it, so this prints what the chunker produced and flags
the things that quietly ruin retrieval: a chunk over the model's window, a chunk so
small it carries no meaning, or a table torn away from its header.

    python scripts\\show_chunks.py                 # summary per document
    python scripts\\show_chunks.py --doc kb-03     # every chunk of one document
    python scripts\\show_chunks.py --children      # the children, with their text
    python scripts\\show_chunks.py --check         # only the warnings

Uses fastembed's tokenizer when it is installed, so the counts are the model's own;
falls back to an estimate otherwise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.chunker import (  # noqa: E402
    MAX_TOKENS,
    MIN_CHILD_TOKENS,
    chunk_corpus,
    estimate_tokens,
)

MODEL = "BAAI/bge-small-en-v1.5"


def token_counter():
    """The embedding model's own tokenizer if available, else the estimate."""
    try:
        from fastembed import TextEmbedding
    except ImportError:
        return estimate_tokens, "estimated (fastembed not installed)"
    model = TextEmbedding(model_name=MODEL)
    tokenizer = getattr(getattr(model, "model", None), "tokenizer", None)
    if tokenizer is None:
        return estimate_tokens, "estimated (tokenizer not exposed)"
    return (lambda text: len(tokenizer.encode(text).ids)), f"{MODEL} tokenizer"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", help="show one document, e.g. kb-03")
    parser.add_argument("--children", action="store_true", help="print child text")
    parser.add_argument("--parents", action="store_true", help="print parent text")
    parser.add_argument("--check", action="store_true", help="warnings only")
    args = parser.parse_args()

    count, how = token_counter()
    chunks = chunk_corpus(count=count)
    children = [c for c in chunks if c.kind == "child"]
    parents = [c for c in chunks if c.kind == "parent"]

    if not args.check:
        print(f"token counts: {how}\n")

    if args.doc:
        for chunk in [c for c in chunks if c.doc_id == args.doc]:
            marker = "parent" if chunk.kind == "parent" else "  child"
            print(f"{marker} {chunk.chunk_id:20} {chunk.tokens:>4} tokens  {chunk.heading[:52]}")
            if (args.children and chunk.kind == "child") or (args.parents and chunk.kind == "parent"):
                print("        " + chunk.text.replace("\n", "\n        ")[:1200] + "\n")
        return

    if not args.check:
        print(f"{'doc':7}{'parents':>9}{'children':>10}{'avg child':>11}{'largest parent':>16}  title")
        for doc_id in sorted({c.doc_id for c in chunks}):
            own = [c for c in chunks if c.doc_id == doc_id]
            kids = [c for c in own if c.kind == "child"]
            dads = [c for c in own if c.kind == "parent"]
            avg = sum(c.tokens for c in kids) // max(len(kids), 1)
            print(f"{doc_id:7}{len(dads):>9}{len(kids):>10}{avg:>11}{max(c.tokens for c in dads):>16}"
                  f"  {own[0].title.split('—')[0].strip()[:40]}")
        print(f"\n{len(parents)} parents, {len(children)} children, "
              f"{sum(c.tokens for c in children)} child tokens in total")
        print(f"child tokens: min {min(c.tokens for c in children)}, "
              f"median {sorted(c.tokens for c in children)[len(children)//2]}, "
              f"max {max(c.tokens for c in children)}")

    problems = []
    for chunk in chunks:
        if chunk.tokens > MAX_TOKENS:
            problems.append(f"over the model window ({chunk.tokens} tokens): {chunk.chunk_id}")
        if chunk.kind == "child" and chunk.tokens < MIN_CHILD_TOKENS:
            problems.append(f"tiny child ({chunk.tokens} tokens): {chunk.chunk_id} — {chunk.text[-60:]!r}")
        if "|---" in chunk.text and chunk.text.count("|") < 6:
            problems.append(f"table header without rows: {chunk.chunk_id}")
    print("\n" + (f"{len(problems)} warnings:" if problems else "no warnings"))
    for problem in problems:
        print("  -", problem)


if __name__ == "__main__":
    main()
