"""Index and retrieval tests, on a small corpus built in a temp folder.

Skipped when the retrieval packages are absent, so the main suite still runs without
them. The embedding model is loaded once for the module — it is the slow part.
"""
import pytest

pytest.importorskip("fastembed", reason="retrieval stack not installed")
pytest.importorskip("chromadb", reason="retrieval stack not installed")
pytest.importorskip("rank_bm25", reason="retrieval stack not installed")

from rag.index import (  # noqa: E402
    MAX_DISTANCE,
    Retriever,
    StaleIndexError,
    build_index,
    corpus_fingerprint,
)

DOCS = {
    "01_codes.md": """---
doc_id: kb-01
title: Response Codes
scope: reference
topic: response codes
confidence: high
---

# Response codes

## Do not honor
Code 05 is a generic issuer refusal with no reason disclosed at all. It can hide a
balance problem, a risk score, an account block or a limit, and the issuer is under no
obligation to say which. An elevated share of it caps how far any diagnosis can go, so
the honest answer names it as undisclosed and takes the question to the issuer rather
than picking one cause and asserting it as though it were evidenced.

## Routing failures
Code 92 means the request could not be routed to an issuer at all. It points at BIN
routing configuration rather than at the cardholder or the issuer, and because routing
tables are organised by card range, an elevated share of it affects whole ranges rather
than individual cardholders. Check the configuration for the affected range before
looking anywhere else, and expect the pattern to be sharply bounded by card range.
""",
    "02_product.md": """---
doc_id: kb-02
title: The Product
scope: product
topic: product overview
confidence: high
---

# The product

## What it is
A compiler that turns a question into a small query language and then into SQL, so the
numbers it reports can be checked rather than merely trusted. The model chooses what to
ask; the compiler decides how to compute it, assembling the statement from fragments
that were written once and reviewed, rather than composing fresh SQL for every question
asked of it.

## What it refuses
It never writes to the database under any circumstances. The language has no statement
that modifies data, the compiler emits only SELECT, and the pipeline refuses to run
anything that does not begin with SELECT. A request to delete or change rows fails at
the language itself, long before it could reach the database connection.
""",
}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    folder = tmp_path_factory.mktemp("knowledge")
    for name, text in DOCS.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


@pytest.fixture(scope="module")
def retriever(corpus, tmp_path_factory):
    store = tmp_path_factory.mktemp("chroma")
    counts = build_index(store=store, folder=corpus, quiet=True)
    assert counts["documents"] == 2 and counts["children"] >= 4
    return Retriever(store=store, folder=corpus)


# --- searching ----------------------------------------------------------------

def test_a_question_finds_its_section(retriever):
    [best] = retriever.search("what does do not honor mean", k=1)
    assert best.doc_id == "kb-01" and "Do not honor" in best.heading
    assert "no reason disclosed" in best.text


def test_a_code_finds_the_right_code(retriever):
    # dense search blurs 05 and 92; BM25 is what keeps them apart
    [best] = retriever.search("what does a high 92 mean", k=1)
    assert "Routing" in best.heading


def test_the_parent_is_returned_not_the_matched_child(retriever):
    [best] = retriever.search("does it write to the database", k=1)
    assert best.chunk_id != best.matched_child       # the child matched, the parent came back
    assert best.text.startswith("kb-02")             # context line intact


def test_out_of_corpus_questions_return_nothing(retriever):
    for question in ["what is the capital of France", "how do I bake bread", "zzz qqq xxx"]:
        assert retriever.search(question) == []


def test_distance_gate_can_be_relaxed(retriever):
    # the floor is a parameter, so a caller can trade precision for recall knowingly
    assert retriever.search("what is the capital of France", max_distance=1.5)


def test_results_are_ordered_and_capped(retriever):
    passages = retriever.search("what does the system refuse to do", k=2)
    assert len(passages) <= 2
    assert passages == sorted(passages, key=lambda p: -p.score)


def test_scope_filters_to_one_kind_of_document(retriever):
    assert all(p.scope == "product" for p in retriever.search("what is it", scope=["product"]))
    assert retriever.search("what does code 92 mean", scope=["product"], k=3) == [] or all(
        p.scope == "product" for p in retriever.search("what does code 92 mean", scope=["product"], k=3)
    )


def test_passages_carry_what_a_citation_needs(retriever):
    [best] = retriever.search("code 92 routing", k=1)
    assert best.citation == f"{best.doc_id} > {best.heading}"
    assert best.confidence == "high"
    assert 0 <= best.distance <= MAX_DISTANCE


# --- staleness ----------------------------------------------------------------

def test_a_changed_corpus_makes_the_index_stale(corpus, tmp_path_factory):
    store = tmp_path_factory.mktemp("chroma-stale")
    build_index(store=store, folder=corpus, quiet=True)
    before = corpus_fingerprint(corpus)

    (corpus / "01_codes.md").write_text(
        DOCS["01_codes.md"] + "\n## Added later\nSomething new.\n", encoding="utf-8"
    )
    assert corpus_fingerprint(corpus) != before
    with pytest.raises(StaleIndexError):
        Retriever(store=store, folder=corpus)

    Retriever(store=store, folder=corpus, check_fingerprint=False)   # opt out, knowingly
    (corpus / "01_codes.md").write_text(DOCS["01_codes.md"], encoding="utf-8")   # restore
