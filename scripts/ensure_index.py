"""Build the retrieval index only if it is missing or out of date.

Runs before the API starts in Docker. The index and the embedding model live in
volumes, so the first start downloads the model and embeds the corpus; every later
start finds a current index and goes straight to the API. Editing anything in
knowledge/ changes the corpus fingerprint, and the next start rebuilds.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.index import COLLECTION, STORE, _client, build_index, corpus_fingerprint  # noqa: E402


def current() -> bool:
    try:
        collection = _client(STORE).get_collection(COLLECTION)
    except Exception:  # no index yet
        return False
    return (collection.metadata or {}).get("fingerprint") == corpus_fingerprint()


def main() -> None:
    if current():
        print(f"retrieval index is current ({STORE}); nothing to build")
        return
    print(f"building the retrieval index in {STORE} (first start, or the docs changed)")
    counts = build_index()
    print(f"indexed {counts['children']} chunks from {counts['documents']} documents")


if __name__ == "__main__":
    main()
