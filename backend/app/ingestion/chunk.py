"""Chunkers. These are experiment variables, not implementation details.

Two strategies, selected by config:

* **`fixed`** -- fixed token windows with overlap. The naive baseline. It is
  deliberately unaware of document structure: it will cut a YAML example in
  half and separate a table row from its header. That failure mode is the thing
  Phase 3 measures an improvement against.

* **`structure_aware`** -- splits on Markdown headings, never inside a fenced
  code block or a table, merges sections too small to stand alone, and splits
  oversized sections at paragraph boundaries. Each chunk carries its heading
  path, which is also prepended to the embedded text so a chunk knows where it
  sits in the document.

Both emit the same `Chunk` shape so retrieval never needs to know which ran.

Both also cut chunk text **verbatim** out of the source, using token offsets to
find the boundaries. An earlier version decoded token ids back to text, which
with bge's uncased WordPiece tokenizer lowercased every chunk and spaced out
its punctuation. See `tokenizer.py` for why that matters, and EXPERIMENTS.md
for what it did to the baseline's numbers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.core.pipeline import CHUNKER_REVISIONS, ChunkingConfig
from app.ingestion.tokenizer import MODEL_MAX_TOKENS, Offsets, Tokenizer

ChunkType = Literal["prose", "code", "table", "mixed"]


def _slice(text: str, offsets: Offsets, start: int, end: int) -> str:
    """The source text covered by tokens `[start, end)`, exactly as written."""
    return text[offsets[start][0] : offsets[end - 1][1]]


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_HAS_WORD = re.compile(r"[^\W_]")  # any letter or digit, in any script


@dataclass
class TextChunk:
    """One chunk, before it becomes a database row."""

    index: int
    text: str
    heading_path: str
    chunk_type: ChunkType
    token_count: int
    meta: dict[str, object] = field(default_factory=dict)

    def embed_text(self, *, prepend_heading: bool) -> str:
        """The string actually handed to the encoder.

        The heading path is prepended here rather than stored in `text`, so the
        displayed citation shows the document's words while the vector reflects
        the context the words sit in.
        """
        if prepend_heading and self.heading_path:
            return f"{self.heading_path}\n\n{self.text}"
        return self.text


class Chunker(Protocol):
    name: str

    def chunk(self, text: str, *, title: str = "") -> list[TextChunk]: ...


# ---------------------------------------------------------------------------
# Block scanning, shared by both chunkers
# ---------------------------------------------------------------------------
@dataclass
class _Block:
    """A line-run that must not be split: a paragraph, a fence, a table."""

    text: str
    kind: ChunkType
    atomic: bool  # True when splitting it would corrupt the content


def _scan_blocks(text: str) -> Iterator[_Block]:
    """Split text into paragraphs, fenced code blocks, and tables."""
    lines = text.split("\n")
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            block = [line]
            i += 1
            while i < n:
                block.append(lines[i])
                if lines[i].lstrip().startswith(marker):
                    i += 1
                    break
                i += 1
            yield _Block("\n".join(block), "code", atomic=True)
            continue

        # A table is a run of pipe rows; the header separator confirms it.
        if _TABLE_ROW.match(line) and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            block = []
            while i < n and _TABLE_ROW.match(lines[i]):
                block.append(lines[i])
                i += 1
            yield _Block("\n".join(block), "table", atomic=True)
            continue

        if not line.strip():
            i += 1
            continue

        block = []
        while i < n and lines[i].strip() and not _FENCE.match(lines[i]):
            block.append(lines[i])
            i += 1
        if block:
            yield _Block("\n".join(block), "prose", atomic=False)


def _classify(blocks: list[_Block]) -> ChunkType:
    kinds = {b.kind for b in blocks}
    if not kinds:
        return "prose"
    if len(kinds) > 1:
        return "mixed"
    return kinds.pop()


def _warn_if_oversized(chunk: TextChunk, limit: int) -> None:
    """An atomic block bigger than the encoder window is truncated silently."""
    if chunk.token_count > limit:
        chunk.meta["exceeds_model_window"] = True
        chunk.meta["model_window"] = limit


# ---------------------------------------------------------------------------
# fixed
# ---------------------------------------------------------------------------
class FixedChunker:
    """Fixed token windows with overlap. The naive baseline."""

    def __init__(self, config: ChunkingConfig, tokenizer: Tokenizer) -> None:
        self.config = config
        self.tokenizer = tokenizer
        self.name = config.label

    def chunk(self, text: str, *, title: str = "") -> list[TextChunk]:
        tokens, offsets = self.tokenizer.encode_with_offsets(text)
        if not tokens:
            return []

        size = self.config.max_tokens
        step = size - self.config.overlap_tokens
        chunks: list[TextChunk] = []

        for start in range(0, len(tokens), step):
            window = tokens[start : start + size]
            if not window:
                break
            # Drop a trailing sliver that is entirely overlap; it adds a
            # near-duplicate row and inflates retrieval candidates.
            if start > 0 and len(window) <= self.config.overlap_tokens:
                break
            body = _slice(text, offsets, start, start + len(window)).strip()
            if not body or (len(window) < self.config.min_tokens and start > 0):
                continue
            chunks.append(
                TextChunk(
                    index=len(chunks),
                    text=body,
                    heading_path=title,
                    chunk_type="prose",  # this chunker cannot tell; that is the point
                    token_count=len(window),
                )
            )
            if start + size >= len(tokens):
                break

        return chunks


# ---------------------------------------------------------------------------
# structure_aware
# ---------------------------------------------------------------------------
class StructureAwareChunker:
    """Heading-aware chunking that respects code blocks and tables."""

    def __init__(self, config: ChunkingConfig, tokenizer: Tokenizer) -> None:
        self.config = config
        self.tokenizer = tokenizer
        self.name = config.label

    def chunk(self, text: str, *, title: str = "") -> list[TextChunk]:
        sections = self._split_sections(text, title)
        chunks: list[TextChunk] = []

        for heading_path, body in sections:
            blocks = list(_scan_blocks(body))
            if not blocks:
                continue
            for piece in self._pack(blocks):
                # A piece with no letter or digit is markup debris -- an empty
                # list bullet where an `{{< include >}}` stood, a lone `#` or
                # `---` -- and embedding it adds a retrievable chunk that says
                # nothing. (Revision 3.)
                if not _HAS_WORD.search(piece.text):
                    continue
                token_count = self.tokenizer.count(piece.text)
                chunk = TextChunk(
                    index=len(chunks),
                    text=piece.text,
                    heading_path=heading_path,
                    chunk_type=piece.kind,
                    token_count=token_count,
                )
                _warn_if_oversized(chunk, MODEL_MAX_TOKENS)
                chunks.append(chunk)

        return self._merge_small(chunks)

    def _split_sections(self, text: str, title: str) -> list[tuple[str, str]]:
        """Split on headings, tracking the full heading path to each section.

        The stack holds `(level, heading)` pairs. A new heading pops every entry
        at its level or deeper, which handles skipped levels (h2 straight to h4)
        without inventing intermediate headings.
        """
        sections: list[tuple[str, str]] = []
        stack: list[tuple[int, str]] = []
        current: list[str] = []
        in_fence = False
        fence_marker = ""

        def path() -> str:
            parts = ([title] if title else []) + [h for _, h in stack]
            return " > ".join(parts)

        current_path = path()

        for line in text.split("\n"):
            fence = _FENCE.match(line)
            if fence:
                marker = fence.group(1)
                if not in_fence:
                    in_fence, fence_marker = True, marker
                elif marker == fence_marker:
                    in_fence, fence_marker = False, ""
                current.append(line)
                continue

            # A '#' inside a fence is a shell comment, not a heading.
            heading = None if in_fence else _HEADING.match(line)
            if not heading:
                current.append(line)
                continue

            if "\n".join(current).strip():
                sections.append((current_path, "\n".join(current)))
            current = []

            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            current_path = path()

        if "\n".join(current).strip():
            sections.append((current_path, "\n".join(current)))
        return sections

    def _pack(self, blocks: list[_Block]) -> list[_Block]:
        """Greedily fill chunks up to max_tokens without splitting atomic blocks."""
        out: list[_Block] = []
        buffer: list[_Block] = []
        buffer_tokens = 0
        limit = self.config.max_tokens

        for block in blocks:
            block_tokens = self.tokenizer.count(block.text)

            if block_tokens > limit:
                if buffer:
                    out.append(
                        _Block("\n\n".join(b.text for b in buffer), _classify(buffer), False)
                    )
                    buffer, buffer_tokens = [], 0
                # An atomic oversized block (a long YAML manifest) is emitted
                # whole: half a manifest is worse than one that gets truncated
                # at embed time, and the chunk is flagged for the record.
                out.extend(
                    [_Block(block.text, block.kind, True)]
                    if block.atomic
                    else self._split_prose(block, limit)
                )
                continue

            if buffer_tokens + block_tokens > limit and buffer:
                out.append(_Block("\n\n".join(b.text for b in buffer), _classify(buffer), False))
                buffer, buffer_tokens = [], 0

            buffer.append(block)
            buffer_tokens += block_tokens

        if buffer:
            out.append(_Block("\n\n".join(b.text for b in buffer), _classify(buffer), False))
        return out

    def _split_prose(self, block: _Block, limit: int) -> list[_Block]:
        """Split an oversized paragraph at token boundaries, with overlap."""
        tokens, offsets = self.tokenizer.encode_with_offsets(block.text)
        step = max(1, limit - self.config.overlap_tokens)
        pieces: list[_Block] = []
        for start in range(0, len(tokens), step):
            end = min(start + limit, len(tokens))
            if start >= end:
                break
            piece = _slice(block.text, offsets, start, end).strip()
            pieces.append(_Block(piece, block.kind, False))
            if end >= len(tokens):
                break
        return pieces

    def _merge_small(self, chunks: list[TextChunk]) -> list[TextChunk]:
        """Fold a too-small chunk into its neighbour under the same heading.

        A two-line section ("### Note" plus a sentence) retrieves badly on its
        own and pollutes the candidate set.
        """
        if not chunks:
            return chunks

        merged: list[TextChunk] = []
        for chunk in chunks:
            if (
                merged
                and chunk.token_count < self.config.min_tokens
                and merged[-1].heading_path == chunk.heading_path
                and merged[-1].token_count + chunk.token_count <= self.config.max_tokens
            ):
                previous = merged[-1]
                previous.text = f"{previous.text}\n\n{chunk.text}"
                previous.token_count += chunk.token_count
                if previous.chunk_type != chunk.chunk_type:
                    previous.chunk_type = "mixed"
                continue
            merged.append(chunk)

        for position, chunk in enumerate(merged):
            chunk.index = position
        return merged


def build_chunker(config: ChunkingConfig, tokenizer: Tokenizer) -> Chunker:
    if config.chunker not in CHUNKER_REVISIONS:
        raise ValueError(f"Unknown chunker: {config.chunker}")
    if config.chunker == "fixed":
        return FixedChunker(config, tokenizer)
    if config.chunker == "structure_aware":
        return StructureAwareChunker(config, tokenizer)
    raise ValueError(f"Unknown chunker: {config.chunker}")
