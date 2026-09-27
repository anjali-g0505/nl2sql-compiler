"""Build the retrieval index: chunk the corpus, embed the children, store in Chroma.

    python scripts\\build_index.py

Rebuilds from scratch each time — the corpus is small, so there is no incremental path
to get wrong. Run it after editing anything in knowledge/, including after
scripts/build_metric_catalogue.py regenerates kb-11.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.index import MODEL_NAME, STORE, build_index, corpus_fingerprint  # noqa: E402


def main() -> None:
    started = time.time()
    counts = build_index()
    print(
        f"indexed {counts['children']} children and {counts['parents']} parents "
        f"from {counts['documents']} documents in {time.time() - started:.1f}s\n"
        f"model: {MODEL_NAME}\nstore: {STORE}\nfingerprint: {corpus_fingerprint()}"
    )


if __name__ == "__main__":
    main()
