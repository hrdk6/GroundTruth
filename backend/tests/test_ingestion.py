"""Parsing, hashing, and chunking.

None of this needs Postgres, which is why it is the part of the pipeline with
the most detailed tests: the behaviours here (never split a code block, ignore
whitespace-only edits) are the ones that quietly corrupt a corpus when wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.pipeline import ChunkingConfig
from app.ingestion.chunk import FixedChunker, StructureAwareChunker, build_chunker
from app.ingestion.fetch import latest_version
from app.ingestion.parse import (
    content_hash,
    derive_url,
    normalize_for_hash,
    parse_file,
    parse_markdown,
)
from app.ingestion.tokenizer import SimpleTokenizer


@pytest.fixture
def tokenizer() -> SimpleTokenizer:
    return SimpleTokenizer()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_front_matter_title_wins_over_heading() -> None:
    raw = "---\ntitle: Pod Lifecycle\nweight: 30\n---\n\n# Something else\n\nBody text."
    doc = parse_markdown(raw, source_path="concepts/pods.md", version="1.28")
    assert doc.title == "Pod Lifecycle"
    assert doc.meta["weight"] == 30
    assert "Something else" in doc.text


def test_falls_back_to_first_heading_without_front_matter() -> None:
    doc = parse_markdown("# Container Probes\n\nText.", source_path="a.md", version="1.28")
    assert doc.title == "Container Probes"


def test_admonition_shortcodes_keep_their_content() -> None:
    raw = "{{< note >}}\nThe kubelet restarts the container.\n{{< /note >}}"
    doc = parse_markdown(raw, source_path="a.md", version="1.28")
    assert "The kubelet restarts the container." in doc.text
    assert "{{<" not in doc.text
    assert "Note:" in doc.text


def test_glossary_tooltip_keeps_the_human_text() -> None:
    raw = 'A {{< glossary_tooltip text="pod" term_id="pod" >}} is the smallest unit.'
    doc = parse_markdown(raw, source_path="a.md", version="1.28")
    assert "A pod is the smallest unit." in doc.text


def test_codenew_becomes_a_visible_placeholder() -> None:
    """A dropped example must not leave the chunk implying it has one."""
    raw = 'See this manifest:\n\n{{< codenew file="pods/simple-pod.yaml" >}}'
    doc = parse_markdown(raw, source_path="a.md", version="1.28")
    assert "pods/simple-pod.yaml" in doc.text
    assert "{{<" not in doc.text


def test_code_blocks_are_never_touched_by_shortcode_stripping() -> None:
    raw = "Text.\n\n```yaml\nvalue: {{ .Values.name }}\ntemplate: {{< keep >}}\n```\n\nMore."
    doc = parse_markdown(raw, source_path="a.md", version="1.28")
    assert "{{ .Values.name }}" in doc.text
    assert "{{< keep >}}" in doc.text


def test_relative_doc_links_become_absolute() -> None:
    raw = "See [the pod docs](/docs/concepts/workloads/pods/) for more."
    doc = parse_markdown(raw, source_path="a.md", version="1.28")
    assert "https://kubernetes.io/docs/concepts/workloads/pods/" in doc.text


def test_external_links_are_left_alone() -> None:
    raw = "See [the spec](https://example.com/spec)."
    doc = parse_markdown(raw, source_path="a.md", version="1.28")
    assert "https://example.com/spec" in doc.text


@pytest.mark.parametrize(
    ("source_path", "expected"),
    [
        (
            "concepts/workloads/pods/_index.md",
            "https://kubernetes.io/docs/concepts/workloads/pods/",
        ),
        ("tasks/debug/debug-pod.md", "https://kubernetes.io/docs/tasks/debug/debug-pod/"),
        ("_index.md", "https://kubernetes.io/docs/"),
    ],
)
def test_url_derivation(source_path: str, expected: str) -> None:
    assert derive_url(source_path) == expected


# ---------------------------------------------------------------------------
# Content hashing (drives incremental ingestion)
# ---------------------------------------------------------------------------
def test_whitespace_only_changes_do_not_change_the_hash() -> None:
    """Upstream reformatting must not trigger a full re-embed."""
    a = "# Title\n\nSome text here.\n"
    b = "# Title\n\n\n\nSome text here.   \n\n"
    assert content_hash(a) == content_hash(b)


def test_line_ending_differences_do_not_change_the_hash() -> None:
    assert content_hash("a\r\nb") == content_hash("a\nb")


def test_real_content_changes_do_change_the_hash() -> None:
    assert content_hash("The default is 10.") != content_hash("The default is 20.")


def test_normalize_is_idempotent() -> None:
    text = "a\n\n\n\nb   \n"
    assert normalize_for_hash(normalize_for_hash(text)) == normalize_for_hash(text)


# ---------------------------------------------------------------------------
# Fixed chunker
# ---------------------------------------------------------------------------
def test_fixed_chunker_respects_window_size(tokenizer: SimpleTokenizer) -> None:
    config = ChunkingConfig(chunker="fixed", max_tokens=10, overlap_tokens=2, min_tokens=2)
    chunker = FixedChunker(config, tokenizer)
    chunks = chunker.chunk(" ".join(f"word{i}" for i in range(50)))

    assert chunks
    assert all(c.token_count <= 10 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_fixed_chunker_overlaps_consecutive_windows(tokenizer: SimpleTokenizer) -> None:
    config = ChunkingConfig(chunker="fixed", max_tokens=10, overlap_tokens=4, min_tokens=2)
    chunker = FixedChunker(config, tokenizer)
    chunks = chunker.chunk(" ".join(f"w{i}" for i in range(30)))

    first = chunks[0].text.split()
    second = chunks[1].text.split()
    assert set(first[-4:]) & set(second[:4]), "consecutive windows should share tokens"


def test_fixed_chunker_name_encodes_parameters(tokenizer: SimpleTokenizer) -> None:
    config = ChunkingConfig(chunker="fixed", max_tokens=256, overlap_tokens=32)
    assert FixedChunker(config, tokenizer).name == "fixed-256-32"


def test_fixed_chunker_returns_nothing_for_empty_text(tokenizer: SimpleTokenizer) -> None:
    assert FixedChunker(ChunkingConfig(), tokenizer).chunk("") == []


# ---------------------------------------------------------------------------
# Structure-aware chunker
# ---------------------------------------------------------------------------
def test_structure_aware_never_splits_a_code_block(tokenizer: SimpleTokenizer) -> None:
    """The headline property: a YAML example survives chunking intact."""
    yaml_block = "\n".join(f"  field{i}: value{i}" for i in range(40))
    text = f"## Example\n\nHere is a manifest:\n\n```yaml\napiVersion: v1\n{yaml_block}\n```\n"

    config = ChunkingConfig(
        chunker="structure_aware", max_tokens=20, overlap_tokens=4, min_tokens=2
    )
    chunks = StructureAwareChunker(config, tokenizer).chunk(text, title="Pods")

    code_chunks = [c for c in chunks if "apiVersion: v1" in c.text]
    assert len(code_chunks) == 1, "the fenced block must land in exactly one chunk"
    assert "field39: value39" in code_chunks[0].text, "and must not be truncated"


def test_structure_aware_never_splits_a_table(tokenizer: SimpleTokenizer) -> None:
    rows = "\n".join(f"| row{i} | value{i} |" for i in range(30))
    text = f"## Options\n\n| Name | Value |\n|------|-------|\n{rows}\n"

    config = ChunkingConfig(
        chunker="structure_aware", max_tokens=20, overlap_tokens=4, min_tokens=2
    )
    chunks = StructureAwareChunker(config, tokenizer).chunk(text, title="Config")

    table_chunks = [c for c in chunks if "| Name | Value |" in c.text]
    assert len(table_chunks) == 1
    assert "row29" in table_chunks[0].text


def test_structure_aware_builds_the_heading_path(tokenizer: SimpleTokenizer) -> None:
    text = (
        "## Pod Lifecycle\n\nIntro paragraph about lifecycle.\n\n"
        "### Container probes\n\nThe kubelet runs probes against containers.\n"
    )
    config = ChunkingConfig(chunker="structure_aware", max_tokens=100, overlap_tokens=10)
    chunks = StructureAwareChunker(config, tokenizer).chunk(text, title="Pods")

    paths = {c.heading_path for c in chunks}
    assert "Pods > Pod Lifecycle" in paths
    assert "Pods > Pod Lifecycle > Container probes" in paths


def test_heading_path_handles_skipped_levels(tokenizer: SimpleTokenizer) -> None:
    """h2 straight to h4 must not invent an intermediate heading."""
    text = "## Top\n\nSome text here for the section.\n\n#### Deep\n\nDeeper text in this part.\n"
    config = ChunkingConfig(chunker="structure_aware", max_tokens=100, overlap_tokens=10)
    chunks = StructureAwareChunker(config, tokenizer).chunk(text, title="Doc")

    deep = [c for c in chunks if "Deeper text" in c.text]
    assert deep
    assert deep[0].heading_path == "Doc > Top > Deep"


def test_sibling_heading_pops_the_stack(tokenizer: SimpleTokenizer) -> None:
    text = (
        "## First\n\n### Nested part here\n\nNested body text.\n\n"
        "## Second\n\nSecond body text goes here.\n"
    )
    config = ChunkingConfig(chunker="structure_aware", max_tokens=100, overlap_tokens=10)
    chunks = StructureAwareChunker(config, tokenizer).chunk(text, title="Doc")

    second = [c for c in chunks if "Second body" in c.text]
    assert second
    assert second[0].heading_path == "Doc > Second", "the h3 must not leak into the next h2"


def test_hash_comment_inside_code_is_not_a_heading(tokenizer: SimpleTokenizer) -> None:
    text = "## Real\n\n```bash\n# This is a shell comment\nkubectl get pods\n```\n"
    config = ChunkingConfig(chunker="structure_aware", max_tokens=200, overlap_tokens=10)
    chunks = StructureAwareChunker(config, tokenizer).chunk(text, title="Doc")

    assert all("shell comment" not in c.heading_path for c in chunks)


def test_structure_aware_merges_tiny_sections(tokenizer: SimpleTokenizer) -> None:
    text = "## Section\n\nShort.\n\nAlso short.\n\nThird short bit.\n"
    config = ChunkingConfig(
        chunker="structure_aware", max_tokens=100, overlap_tokens=10, min_tokens=20
    )
    chunks = StructureAwareChunker(config, tokenizer).chunk(text, title="Doc")
    assert len(chunks) == 1, "fragments under one heading should merge"


def test_oversized_code_block_is_flagged_not_silently_truncated(
    tokenizer: SimpleTokenizer,
) -> None:
    huge = "\n".join(f"line{i}: value{i}" for i in range(2000))
    text = f"## Big\n\n```yaml\n{huge}\n```\n"
    config = ChunkingConfig(chunker="structure_aware", max_tokens=400, overlap_tokens=20)
    chunks = StructureAwareChunker(config, tokenizer).chunk(text, title="Doc")

    oversized = [c for c in chunks if c.meta.get("exceeds_model_window")]
    assert oversized, "a block past the encoder window must be marked, not passed off as fine"


def test_embed_text_prepends_heading_path(tokenizer: SimpleTokenizer) -> None:
    config = ChunkingConfig(chunker="structure_aware", max_tokens=100, overlap_tokens=10)
    chunks = StructureAwareChunker(config, tokenizer).chunk(
        "## Probes\n\nThe kubelet runs probes.\n", title="Pods"
    )
    chunk = chunks[0]
    assert chunk.embed_text(prepend_heading=True).startswith("Pods > Probes")
    assert chunk.embed_text(prepend_heading=False) == chunk.text
    assert "Pods > Probes" not in chunk.text, "display text must stay the document's own words"


def test_build_chunker_selects_by_config(tokenizer: SimpleTokenizer) -> None:
    assert isinstance(build_chunker(ChunkingConfig(chunker="fixed"), tokenizer), FixedChunker)
    assert isinstance(
        build_chunker(ChunkingConfig(chunker="structure_aware"), tokenizer),
        StructureAwareChunker,
    )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def test_latest_version_is_numeric_not_lexicographic() -> None:
    assert latest_version(["1.9", "1.10", "1.26"]) == "1.26"
    assert latest_version(["1.26", "1.28", "1.30"]) == "1.30"


def test_parse_file_skips_empty_documents(tmp_path: Path) -> None:
    path = tmp_path / "empty.md"
    path.write_text("---\ntitle: Nothing\n---\n\n", encoding="utf-8")
    assert parse_file(path, root=tmp_path, version="1.28") is None
