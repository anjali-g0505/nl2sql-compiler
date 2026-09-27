"""Embed the chunks, store them in Chroma, and search them.

    build_index()                 chunk -> embed -> store, once
    Retriever().search(question)  question -> passages to put in a prompt

What is stored, and why:
  * **Children are embedded and searched**; parents are stored unembedded and returned.
    A short passage matches a question sharply, but a whole section is what reads well
    in a prompt, so a hit on a child is answered with its parent (parent-document
    retrieval).
  * **Search is hybrid.** Dense vectors miss literal tokens — `92` and `91`, `TD_BD`,
    `decline_rate` all sit in nearly the same place in embedding space — so BM25 runs
    beside the vectors and the two rankings are fused. Neither alone is enough here.
  * **A corpus fingerprint is stored with the collection.** Editing a document or
    regenerating the metric catalogue leaves the index stale, and a stale index fails
    silently, which is the worst way to fail.

The embedding model runs locally (fastembed, ONNX) and truncates at 512 tokens, which
is what the chunker's budgets are for.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from rag.chunker import KNOWLEDGE, Chunk, chunk_corpus, load_documents

MODEL_NAME = "BAAI/bge-small-en-v1.5"
STORE = Path(__file__).resolve().parent.parent / "knowledge" / ".chroma"
COLLECTION = "intentql-knowledge"
CANDIDATES = 12       # per retriever, before fusion
RRF_K = 60            # the usual reciprocal-rank-fusion constant
# Fused ranks say which passage is best, never whether any of them is relevant: a
# question about sourdough still produces a ranking. The cosine distance of the best
# dense match is what separates "in this corpus" from "not in it", measured on real
# questions (~0.25-0.35) against nonsense (~0.50+).
MAX_DISTANCE = 0.42


def corpus_fingerprint(folder: Path = KNOWLEDGE) -> str:
    """Hash of every document, so a stale index can be detected rather than trusted."""
    digest = hashlib.sha256()
    for path in load_documents(folder):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


class _Embedder:
    """fastembed, loaded once. Documents and questions are embedded differently:
    bge models expect an instruction prefix on the query side only."""

    def __init__(self, model_name: str = MODEL_NAME):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model_name)

    def documents(self, texts: Sequence[str]) -> List[List[float]]:
        return [vector.tolist() for vector in self.model.embed(list(texts))]

    def query(self, text: str) -> List[float]:
        return [vector.tolist() for vector in self.model.query_embed([text])][0]


def _client(store: Path = STORE):
    import chromadb

    store.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(store))


def build_index(store: Path = STORE, folder: Path = KNOWLEDGE, quiet: bool = False) -> Dict[str, int]:
    """Chunk, embed the children, and write everything to Chroma. Rebuilds from scratch."""
    embedder = _Embedder()
    chunks = chunk_corpus(folder, count=_model_token_counter(embedder))
    children = [c for c in chunks if c.kind == "child"]
    parents = [c for c in chunks if c.kind == "parent"]

    client = _client(store)
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        name=COLLECTION,
        # cosine, so a distance reads directly as 1 - similarity and can be thresholded
        metadata={
            "fingerprint": corpus_fingerprint(folder),
            "model": MODEL_NAME,
            "hnsw:space": "cosine",
        },
    )

    if not quiet:
        print(f"embedding {len(children)} children with {MODEL_NAME} …")
    vectors = embedder.documents([c.text for c in children])
    collection.add(
        ids=[c.chunk_id for c in children],
        documents=[c.text for c in children],
        embeddings=vectors,
        metadatas=[c.metadata() for c in children],
    )
    # parents are stored to be fetched by id, never searched, so they get a zero vector
    collection.add(
        ids=[p.chunk_id for p in parents],
        documents=[p.text for p in parents],
        embeddings=[[0.0] * len(vectors[0])] * len(parents),
        metadatas=[p.metadata() for p in parents],
    )
    return {"children": len(children), "parents": len(parents), "documents": len(set(c.doc_id for c in chunks))}


def _model_token_counter(embedder: _Embedder):
    tokenizer = getattr(getattr(embedder.model, "model", None), "tokenizer", None)
    if tokenizer is None:
        from rag.chunker import estimate_tokens

        return estimate_tokens
    return lambda text: len(tokenizer.encode(text).ids)


@dataclass(frozen=True)
class Passage:
    """One retrieved section, ready to be cited and put in a prompt."""

    chunk_id: str
    text: str
    doc_id: str
    heading: str
    scope: str
    confidence: str
    score: float
    distance: float          # cosine distance of the best matching child
    matched_child: str

    @property
    def citation(self) -> str:
        return f"{self.doc_id} > {self.heading}" if self.heading else self.doc_id


class StaleIndexError(RuntimeError):
    """The corpus changed since the index was built."""


class Retriever:
    """Hybrid search over the chunk store."""

    def __init__(self, store: Path = STORE, folder: Path = KNOWLEDGE, check_fingerprint: bool = True):
        from rank_bm25 import BM25Okapi

        self.collection = _client(store).get_collection(COLLECTION)
        built = (self.collection.metadata or {}).get("fingerprint")
        if check_fingerprint and built != corpus_fingerprint(folder):
            raise StaleIndexError(
                "the knowledge corpus changed since the index was built; "
                "run scripts/build_index.py"
            )
        self.embedder = _Embedder()

        stored = self.collection.get(include=["documents", "metadatas"])
        self.children: Dict[str, Tuple[str, dict]] = {}
        self.parents: Dict[str, Tuple[str, dict]] = {}
        for chunk_id, text, meta in zip(stored["ids"], stored["documents"], stored["metadatas"]):
            target = self.children if meta["kind"] == "child" else self.parents
            target[chunk_id] = (text, meta)

        self._bm25_ids = list(self.children)
        self._bm25 = BM25Okapi([_tokenize(self.children[i][0]) for i in self._bm25_ids])

    # --- the two rankings -----------------------------------------------------

    def _dense(self, question: str, where: Optional[dict], k: int) -> Dict[str, float]:
        """Child id -> cosine distance, best first."""
        result = self.collection.query(
            query_embeddings=[self.embedder.query(question)],
            n_results=k,
            where={"$and": [{"kind": "child"}, where]} if where else {"kind": "child"},
        )
        return dict(zip(result["ids"][0], result["distances"][0]))

    def _sparse(self, question: str, where: Optional[dict], k: int) -> List[str]:
        scores = self._bm25.get_scores(_tokenize(question))
        ranked = sorted(zip(self._bm25_ids, scores), key=lambda pair: -pair[1])
        allowed = [i for i, score in ranked if score > 0 and self._matches(i, where)]
        return allowed[:k]

    def _matches(self, chunk_id: str, where: Optional[dict]) -> bool:
        if not where:
            return True
        meta = self.children[chunk_id][1]
        return all(meta.get(key) == value for key, value in where.items())

    # --- fusion ---------------------------------------------------------------

    def search(
        self,
        question: str,
        k: int = 4,
        scope: Optional[Sequence[str]] = None,
        max_distance: float = MAX_DISTANCE,
    ) -> List[Passage]:
        """Top passages for a question: hybrid search on children, parents returned.

        `scope` filters on the documents' declared scope, e.g. ("definitions",
        "reference") for a lookup and ("reasoning playbook", "guardrails") for an
        explanation. Returns [] when even the closest passage is further than
        `max_distance`, which is the signal to say the corpus has no answer rather than
        stretch the nearest chunk into one.
        """
        where = {"scope": {"$in": list(scope)}} if scope else None
        distances = self._dense(question, where, CANDIDATES)
        if not distances or min(distances.values()) > max_distance:
            return []                      # nothing in the corpus is about this
        sparse = self._sparse(question, where, CANDIDATES)

        fused: Dict[str, float] = {}
        for ranking in (list(distances), sparse):
            for position, chunk_id in enumerate(ranking):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + position + 1)

        # a child stands in for its parent; the best-scoring child wins the section
        best: Dict[str, Tuple[float, str]] = {}
        for chunk_id, score in fused.items():
            parent_id = self.children[chunk_id][1].get("parent_id") or chunk_id
            if score > best.get(parent_id, (0.0, ""))[0]:
                best[parent_id] = (score, chunk_id)

        passages = []
        for parent_id, (score, child_id) in sorted(best.items(), key=lambda kv: -kv[1][0])[:k]:
            text, meta = self.parents.get(parent_id, self.children[child_id])
            passages.append(Passage(
                chunk_id=parent_id, text=text, doc_id=meta["doc_id"], heading=meta["heading"],
                scope=meta["scope"], confidence=meta["confidence"], score=round(score, 4),
                distance=round(distances.get(child_id, 1.0), 3), matched_child=child_id,
            ))
        return passages


def _tokenize(text: str) -> List[str]:
    """Lowercased words and codes. Keeps `92`, `TD_BD` and `decline_rate` intact, which
    is the whole point of having BM25 beside the vectors."""
    import re

    return re.findall(r"[a-z0-9_]+", text.lower())
