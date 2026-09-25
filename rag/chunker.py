"""Split the knowledge corpus into chunks for retrieval.

Two sizes, for two jobs (parent-document retrieval):

    child   a paragraph or a group of table rows, ~60-120 tokens. What gets embedded
            and searched, because a small passage matches a question sharply.
    parent  the whole `##` section the child came from. What gets put in the prompt,
            because a paragraph read alone usually loses the point of its section.

Rules that the corpus itself forces:
  * The embedding model truncates at 512 tokens with no warning, so a parent is split
    rather than allowed to overflow, and every child is well inside the limit.
  * A table is never split from its header row: a group of rows without the header is
    unreadable, and the header alone is noise.
  * Sections shorter than MIN_SECTION_TOKENS are merged into the previous one, so a
    heading with two lines under it does not become its own chunk.
  * Every chunk carries a context line ("kb-03 Response codes > Reading the classes"),
    because a paragraph beginning "These are issuer decisions" is unretrievable alone.
  * The manifest (kb-00) is metadata about the corpus and is excluded.

Nothing here embeds or stores; that is rag/index.py. This module is pure text handling
so it can be tested and eyeballed on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence

KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"
EXCLUDED = ("00_",)          # the manifest describes the corpus; it is not corpus
MAX_TOKENS = 480             # under the model's 512, leaving room for the context line
TARGET_CHILD_TOKENS = 110    # what a child aims for; paragraphs are usually close
MIN_SECTION_TOKENS = 45      # shorter sections are merged into the previous one
MIN_CHILD_TOKENS = 25        # shorter children are merged into the previous child

FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S) #this regex matches the frontmatter section of a markdown document, which is typically enclosed between '---' lines. The re.S flag allows the dot (.) to match newline characters, enabling the regex to capture multi-line content within the frontmatter.
""" Example: ---
doc_id: kb-09
title: Reading Query Result Tables and Charts
scope: interpretation aid
topic: result interpretation
confidence: high (project conventions)
source: project output conventions
last_updated: 2026-09-23
---
"""
FIELD = re.compile(r"^(\w+):\s*(.*)$", re.M) #this regex matches individual fields within the frontmatter.
TABLE_ROW = re.compile(r"^\s*\|") #this regex matches lines that represent rows in a markdown table, which typically start with a vertical bar (|) character, possibly preceded by whitespace.


def estimate_tokens(text: str) -> int:
    """Rough token count, used when no real tokenizer is supplied.

    Deliberately over-estimates: for this corpus a word is about 1.4 tokens once
    identifiers, code spans and punctuation are counted, and running slightly over
    budget is safer than silently truncating at the model.
    """
    return int(len(text.split()) * 1.4) + 1 #count number of tokens by splitting text into words based on whitespace and multiplying by 1.4


@dataclass(frozen=True)
class Chunk:
    """One passage, with everything retrieval and citation need."""

    chunk_id: str
    text: str                      # what gets embedded or shown, context line included
    doc_id: str
    title: str
    heading: str
    scope: str
    topic: str
    confidence: str                # "high", "medium" or "low"
    kind: str                      # "child" or "parent"
    parent_id: Optional[str] = None
    tokens: int = 0

    def metadata(self) -> Dict[str, str]:
        """Flat values only — what a vector store accepts as metadata."""
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "heading": self.heading,
            "scope": self.scope,
            "topic": self.topic,
            "confidence": self.confidence,
            "kind": self.kind,
            "parent_id": self.parent_id or "",
            "tokens": str(self.tokens),
        }


@dataclass
class _Section:
    heading: str
    lines: List[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n".join(self.lines).strip()


def load_documents(folder: Path = KNOWLEDGE) -> Iterator[Path]: # this function goes over each markdown file in the knowledge folder except for the excluded one and gives their pat
    for path in sorted(folder.glob("*.md")):
        if not path.name.startswith(EXCLUDED):
            yield path


def parse_frontmatter(text: str) -> Dict[str, str]: 
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    return {k: v.strip() for k, v in FIELD.findall(match.group(1))} #this function extracts the fields (key-value pairs) from  the frontematter text usign the frontmatter regex.

def split_sections(body: str) -> List[_Section]:
    """Split on `##` headings. Text before the first one joins the document title.
       It outputs a list of _Section objects, each containing a heading of the section and a list of lines under the section."""
    sections: List[_Section] = []
    current = _Section(heading="")
    for line in body.split("\n"):
        if line.startswith("## "):
            if current.body: #current is the current section being built. If it has any content (body), it is added to the sections list before starting a new section with the new heading.
                sections.append(current)
            current = _Section(heading=line[3:].strip())
        elif line.startswith("# "):
            continue                       # the document title starts with # and lives in frontmatter
        else:
            current.lines.append(line)
    if current.body:
        sections.append(current)
    return sections


def merge_small_sections(sections: Sequence[_Section], count: Callable[[str], int]) -> List[_Section]:
    """A heading with a line or two under it belongs to the section before it."""
    merged: List[_Section] = []
    for section in sections:
        too_small = count(section.body) < MIN_SECTION_TOKENS
        if too_small and merged:
            previous = merged[-1]
            previous.lines += ["", f"### {section.heading}" if section.heading else "", *section.lines]
        else:
            merged.append(_Section(section.heading, list(section.lines)))
    return merged


def split_blocks(body: str) -> List[str]:
    """Paragraphs, with a table kept as one block including its header."""
    blocks: List[str] = [] #this is a list that will hold the final blocks of text, which can be paragraphs or tables.
    buffer: List[str] = [] #this is a temporary list that accumulates lines of text until a complete block is formed. 
    in_table = False
    for line in body.split("\n"):
        is_row = bool(TABLE_ROW.match(line)) #table row is the the first row - that starts with a |
        if is_row and not in_table and buffer and buffer[-1].strip():
            blocks.append("\n".join(buffer).strip())   # text right before a table
            buffer = [] #since new block is starting
        if not line.strip() and not in_table: #a blank line outside a table is a paragraph break, so the current buffer is added to blocks and the buffer is reset for the next block.
            if buffer:
                blocks.append("\n".join(buffer).strip())
                buffer = []
            continue
        if in_table and not is_row: #this is the last row of hte table
            blocks.append("\n".join(buffer).strip())
            buffer = []
            in_table = False
            if not line.strip():
                continue
        in_table = is_row
        buffer.append(line)
    if buffer:
        blocks.append("\n".join(buffer).strip())
    return [b for b in blocks if b]


def split_table(block: str, count: Callable[[str], int], target: int = TARGET_CHILD_TOKENS) -> List[str]:
    """Break a long table into row groups, repeating the header on each group."""
    lines = block.split("\n")
    header = lines[:2] if len(lines) > 1 and set(lines[1].strip()) <= set("|-: ") else lines[:1]
    rows = lines[len(header):]
    groups, current = [], []
    for row in rows:
        # cut before the row that would overflow, not after it
        if current and count("\n".join(header + current + [row])) > target:
            groups.append("\n".join(header + current))
            current = []
        current.append(row)
    if current:
        groups.append("\n".join(header + current))
    return groups or [block]


LIST_ITEM = re.compile(r"^\s*([-*]|\d+\.)\s")


def split_paragraph(block: str, count: Callable[[str], int], target: int = MAX_TOKENS) -> List[str]:
    """Split an over-long block, never mid-sentence.

    Lists are broken on line ends and prose on sentence ends, because a numbered list
    has no blank lines and would otherwise stay one indivisible block.
    """
    if count(block) <= target:
        return [block]
    # a block that already has line breaks (a list, mostly) is split on them, so the
    # pieces keep the layout they were written with; prose is split on sentence ends
    multiline = "\n" in block.strip()
    #the below part of hte code splits the blocks into smaller pieces based on whether it is multiline or not. If it is multiline, it splits the block into lines using the newline character as the delimiter. If it is not multiline, it uses a regular expression to split the block into sentences by looking for punctuation marks (., !, ?) followed by whitespace. The resulting parts are then processed to create smaller pieces that do not exceed the target token count.
    parts = block.split("\n") if multiline else re.split(r"(?<=[.!?])\s+", block) #this regex splits the block into sentences by looking for punctuation marks (., !, ?) followed by whitespace. 
    joiner = "\n" if multiline else " " 
    pieces, current = [], ""
    for part in parts:
        candidate = f"{current}{joiner}{part}".strip() if current else part
        if current and count(candidate) > target:
            pieces.append(current)
            current = part
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def context_line(doc_id: str, title: str, heading: str) -> str:
    """Prefix that makes a passage searchable when its own text says only "these"."""
    short_title = title.split("—")[0].strip()
    return f"{doc_id} {short_title}" + (f" > {heading}" if heading else "")


def chunk_document(path: Path, count: Callable[[str], int] = estimate_tokens) -> List[Chunk]:
    text = path.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    body = FRONTMATTER.sub("", text, count=1)
    doc_id = meta.get("doc_id", path.stem)
    title = meta.get("title", path.stem)

    chunks: List[Chunk] = []
    sections = merge_small_sections(split_sections(body), count)
    for number, section in enumerate(sections, start=1):
        prefix = context_line(doc_id, title, section.heading)
        parent_id = f"{doc_id}#{number}"
        parent_text = f"{prefix}\n\n{section.body}".strip()

        def make(chunk_id: str, content: str, kind: str, parent: Optional[str]) -> Chunk:
            return Chunk(
                chunk_id=chunk_id, text=content, doc_id=doc_id, title=title,
                heading=section.heading, scope=meta.get("scope", ""), topic=meta.get("topic", ""),
                confidence=meta.get("confidence", ""), kind=kind, parent_id=parent,
                tokens=count(content),
            )

        # a parent longer than the model's window is split, never truncated
        if count(parent_text) > MAX_TOKENS:
            budget = MAX_TOKENS - count(prefix)
            parts = _pack(_explode(section.body, count, budget), count, budget)
            parents = [make(f"{parent_id}.{i}", f"{prefix}\n\n{part}", "parent", None)
                       for i, part in enumerate(parts, start=1)]
        else:
            parents = [make(parent_id, parent_text, "parent", None)]
        chunks += parents

        for parent in parents:
            own_body = parent.text[len(prefix):].strip()
            children = _pack(_explode(own_body, count, TARGET_CHILD_TOKENS), count, TARGET_CHILD_TOKENS)
            for i, child in enumerate(children, start=1):
                chunks.append(make(f"{parent.chunk_id}:c{i}", f"{prefix}\n\n{child}", "child", parent.chunk_id))
    return chunks


def _explode(body: str, count: Callable[[str], int], target: int) -> List[str]:
    """Body -> pieces no larger than `target`: tables by row group, prose by sentence."""
    pieces: List[str] = []
    for block in split_blocks(body):
        if TABLE_ROW.match(block):
            pieces += split_table(block, count, target)
        elif count(block) > target:
            pieces += split_paragraph(block, count, target)
        else:
            pieces.append(block)
    return [piece for oversized in pieces for piece in _hard_split(oversized, count, target)]


def _hard_split(piece: str, count: Callable[[str], int], target: int) -> List[str]:
    """Last resort for something indivisible — one enormous table row, say.

    Splitting mid-sentence is ugly, but the alternative is the model truncating the
    tail without saying so.
    """
    if count(piece) <= MAX_TOKENS:
        return [piece]
    words, out, current = piece.split(" "), [], []
    for word in words:
        current.append(word)
        if count(" ".join(current)) >= target:
            out.append(" ".join(current))
            current = []
    if current:
        out.append(" ".join(current))
    return out


def _pack(pieces: Sequence[str], count: Callable[[str], int], budget: int) -> List[str]:
    """Greedily join pieces up to a budget, so tiny fragments never stand alone."""
    packed: List[str] = []
    for piece in pieces:
        if packed and count(packed[-1]) < MIN_CHILD_TOKENS:
            packed[-1] = f"{packed[-1]}\n\n{piece}"
        elif packed and count(f"{packed[-1]}\n\n{piece}") <= budget:
            packed[-1] = f"{packed[-1]}\n\n{piece}"
        else:
            packed.append(piece)
    # a trailing fragment has nothing after it to absorb it, so fold it backwards
    if len(packed) > 1 and count(packed[-1]) < MIN_CHILD_TOKENS:
        tail = packed.pop()
        packed[-1] = f"{packed[-1]}\n\n{tail}"
    return packed


def chunk_corpus(folder: Path = KNOWLEDGE, count: Callable[[str], int] = estimate_tokens) -> List[Chunk]:
    return [chunk for path in load_documents(folder) for chunk in chunk_document(path, count)]
