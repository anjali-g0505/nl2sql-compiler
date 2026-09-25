"""Chunker tests: the rules the corpus forces, checked on the real knowledge/ files.

Token counts use the estimate, not the model's tokenizer, so these run without
fastembed installed and without downloading anything.
"""
import pytest

from rag.chunker import (
    MAX_TOKENS,
    MIN_CHILD_TOKENS,
    Chunk,
    chunk_corpus,
    chunk_document,
    context_line,
    estimate_tokens,
    load_documents,
    parse_frontmatter,
    split_blocks,
    split_paragraph,
    split_sections,
    split_table,
)

CORPUS = chunk_corpus()
CHILDREN = [c for c in CORPUS if c.kind == "child"]
PARENTS = [c for c in CORPUS if c.kind == "parent"]


# --- the corpus ---------------------------------------------------------------

def test_the_manifest_is_excluded():
    # kb-00 describes the corpus; embedding it would pollute retrieval
    assert "kb-00" not in {c.doc_id for c in CORPUS}
    assert all(not p.name.startswith("00_") for p in load_documents())


def test_every_document_produces_chunks():
    documents = {c.doc_id for c in CORPUS}
    assert len(documents) == len(list(load_documents()))
    assert {"kb-03", "kb-11", "kb-15"} <= documents


def test_nothing_exceeds_the_embedding_window():
    # the model truncates silently at 512, so an over-long chunk loses its tail
    assert max(c.tokens for c in CORPUS) <= MAX_TOKENS


def test_no_chunk_is_too_small_to_mean_anything():
    assert min(c.tokens for c in CHILDREN) >= MIN_CHILD_TOKENS


def test_children_point_at_a_parent_that_contains_them():
    parents = {p.chunk_id: p for p in PARENTS}
    for child in CHILDREN:
        assert child.parent_id in parents
        body = child.text.split("\n\n", 1)[1]
        assert body.split("\n")[0] in parents[child.parent_id].text


# --- context and metadata ------------------------------------------------------

def test_every_chunk_starts_with_its_context_line():
    for chunk in CORPUS:
        first = chunk.text.split("\n")[0]
        assert first.startswith(chunk.doc_id)
        if chunk.heading:
            assert chunk.heading in first


def test_context_line_shape():
    assert context_line("kb-03", "Response Codes — long subtitle", "Reading the classes") == (
        "kb-03 Response Codes > Reading the classes"
    )


def test_metadata_is_flat_strings_for_the_vector_store():
    for value in CORPUS[0].metadata().values():
        assert isinstance(value, str)


def test_frontmatter_travels_into_the_chunks():
    codes = next(c for c in CORPUS if c.doc_id == "kb-03")
    assert codes.scope == "reference" and codes.topic == "response codes"
    assert codes.confidence  # kb-03 carries a mixed-confidence note


# --- tables --------------------------------------------------------------------

TABLE = """| Code | Meaning |
|---|---|
| 05 | Do not honor |
| 51 | Insufficient funds |
| 91 | Issuer inoperative |"""


def test_a_table_is_one_block_not_many_paragraphs():
    assert split_blocks(f"Intro line.\n\n{TABLE}\n\nAfter.") == ["Intro line.", TABLE, "After."]


def test_every_table_group_repeats_the_header():
    groups = split_table(TABLE, estimate_tokens, target=12)
    assert len(groups) > 1
    assert all(group.startswith("| Code | Meaning |\n|---|---|") for group in groups)
    assert "".join(groups).count("Do not honor") == 1   # rows are not duplicated


def test_table_rows_are_never_orphaned_from_their_header():
    for chunk in CORPUS:
        if "|---" in chunk.text:
            rows = [line for line in chunk.text.split("\n") if line.strip().startswith("|")]
            assert len(rows) >= 3          # header, separator, and at least one row


# --- splitting prose -----------------------------------------------------------

def test_short_text_is_left_alone():
    assert split_paragraph("One short sentence.", estimate_tokens) == ["One short sentence."]


def test_long_prose_splits_on_sentence_ends():
    text = " ".join(f"Sentence number {i} says something about declines." for i in range(40))
    pieces = split_paragraph(text, estimate_tokens, target=60)
    assert len(pieces) > 1
    assert all(p.endswith(".") for p in pieces)
    assert " ".join(pieces) == text          # nothing lost, nothing duplicated


def test_a_numbered_list_splits_on_line_ends():
    listing = "\n".join(f"{i}. **Point {i}.** " + "word " * 30 for i in range(1, 9))
    pieces = split_paragraph(listing, estimate_tokens, target=80)
    assert len(pieces) > 1
    assert all(piece.lstrip().startswith(tuple("12345678")) for piece in pieces)


# --- sections ------------------------------------------------------------------

def test_sections_split_on_second_level_headings():
    sections = split_sections("# Title\n\nLead in.\n\n## First\n\nA.\n\n## Second\n\nB.")
    assert [s.heading for s in sections] == ["", "First", "Second"]


def test_a_tiny_section_is_merged_into_the_previous_one(tmp_path):
    document = tmp_path / "99_test.md"
    document.write_text(
        "---\ndoc_id: kb-99\ntitle: Test\nscope: test\ntopic: test\nconfidence: high\n---\n\n"
        "# Test\n\n## Big\n\n" + "word " * 200 + "\n\n## Tiny\n\nTwo words only.\n",
        encoding="utf-8",
    )
    headings = [c.heading for c in chunk_document(document) if c.kind == "parent"]
    assert "Tiny" not in headings
    assert any("Tiny" in c.text for c in chunk_document(document))   # kept, not dropped


def test_parse_frontmatter():
    meta = parse_frontmatter("---\ndoc_id: kb-01\nscope: definitions\n---\n\nbody\n")
    assert meta == {"doc_id": "kb-01", "scope": "definitions"}


@pytest.mark.parametrize("text, at_least", [("", 1), ("one two three", 4)])
def test_token_estimate_never_returns_zero(text, at_least):
    assert estimate_tokens(text) >= at_least


def test_chunk_is_frozen():
    with pytest.raises(Exception):
        CORPUS[0].text = "changed"        # type: ignore[misc]


def test_chunk_ids_are_unique():
    ids = [c.chunk_id for c in CORPUS]
    assert len(ids) == len(set(ids))
