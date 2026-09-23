"""Parse a Kubernetes documentation Markdown file.

The Kubernetes docs are Hugo, so the raw Markdown carries machinery that would
otherwise end up in chunks and be retrieved as if it were prose:

* YAML front matter (title, weight, `content_type`),
* Hugo shortcodes -- `{{< note >}}`, `{{% capture %}}`, `{{< codenew file=... >}}`,
* relative links that mean nothing once the text is out of the site.

The rule applied here: **shortcodes that wrap content are unwrapped, shortcodes
that are markup are dropped.** A `{{< note >}}...{{< /note >}}` block holds real
documentation and becomes a "Note:" paragraph; a `{{< codenew >}}` reference
points at a file we do not have and becomes a readable placeholder rather than
silent deletion, so a chunk never claims to contain an example it lacks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter

from app.core.logging import get_logger

log = get_logger(__name__)

DOCS_BASE_URL = "https://kubernetes.io/docs/"

# {{< note >}} ... {{< /note >}} and the older {{% note %}} form.
_ADMONITION_NAMES = ("note", "caution", "warning", "tip", "important")
_ADMONITION_OPEN = re.compile(
    r"\{\{[<%]\s*(" + "|".join(_ADMONITION_NAMES) + r")\s*[>%]\}\}", re.IGNORECASE
)
_ADMONITION_CLOSE = re.compile(
    r"\{\{[<%]\s*/\s*(" + "|".join(_ADMONITION_NAMES) + r")\s*[>%]\}\}", re.IGNORECASE
)

# {{< codenew file="pods/simple-pod.yaml" >}} -> a named example we do not inline.
_CODENEW = re.compile(
    r"\{\{[<%]\s*(?:codenew|code_sample|code)\s+(?:file=)?\"?([^\"\s>%}]+)\"?[^>%}]*[>%]\}\}",
    re.IGNORECASE,
)

# {{< glossary_tooltip text="pod" term_id="pod" >}} -> keep the human text.
_GLOSSARY = re.compile(
    r"\{\{[<%]\s*glossary_tooltip[^>%}]*?text=\"([^\"]+)\"[^>%}]*[>%]\}\}", re.IGNORECASE
)
_GLOSSARY_TERM_ONLY = re.compile(
    r"\{\{[<%]\s*glossary_tooltip[^>%}]*?term_id=\"([^\"]+)\"[^>%}]*[>%]\}\}", re.IGNORECASE
)

# {{% capture overview %}} / {{< feature-state ... >}} and any other leftover.
_CAPTURE = re.compile(r"\{\{[<%]\s*/?\s*capture[^>%}]*[>%]\}\}", re.IGNORECASE)
_FEATURE_STATE = re.compile(
    r"\{\{[<%]\s*feature-state[^>%}]*?(?:for_k8s_version=\"([^\"]*)\")?[^>%}]*?"
    r"state=\"([^\"]*)\"[^>%}]*[>%]\}\}",
    re.IGNORECASE,
)
_ANY_SHORTCODE = re.compile(r"\{\{[<%][^}]*?[>%]\}\}", re.DOTALL)

# `## {{% heading "prerequisites" %}}` renders as a localized section title.
# Stripped like any other shortcode it left an *empty* heading -- on 266 of the
# evaluated pages -- so "Before you begin" and "What's next" shared one
# heading path, were merged as if they were one section, and were paired
# against each other by conflict detection. These are the site's English
# labels (kubernetes/website data/i18n/en/en.toml).
_HEADING_SHORTCODE = re.compile(r"\{\{[<%]\s*heading\s+\"([^\"]+)\"\s*[>%]\}\}", re.IGNORECASE)
_HEADING_LABELS = {
    "prerequisites": "Before you begin",
    "whatsnext": "What's next",
    "objectives": "Objectives",
    "cleanup": "Clean up",
    "seealso": "See also",
    "synopsis": "Synopsis",
    "options": "Options",
}

_FENCE = re.compile(r"^(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# [text](/docs/concepts/) -> absolute; leaves external and anchor links alone.
_RELATIVE_LINK = re.compile(r"\[([^\]]*)\]\((/docs/[^)]*)\)")


@dataclass
class ParsedDocument:
    source_path: str
    version: str
    title: str
    url: str
    text: str
    content_hash: str
    meta: dict[str, Any] = field(default_factory=dict)


def _strip_html_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Remove `<!-- ... -->` from one line, carrying multi-line state across lines."""
    kept: list[str] = []
    position = 0
    while position < len(line):
        if in_comment:
            end = line.find("-->", position)
            if end == -1:
                return "".join(kept), True
            position, in_comment = end + 3, False
        else:
            start = line.find("<!--", position)
            if start == -1:
                kept.append(line[position:])
                break
            kept.append(line[position:start])
            position, in_comment = start + 4, True
    return "".join(kept), in_comment


def _strip_shortcodes(text: str) -> str:
    """Unwrap content-bearing shortcodes, drop pure markup and HTML comments.

    Fenced code blocks are left completely alone: a YAML example may legitimately
    contain brace sequences, and mangling an example is worse than leaving a
    stray shortcode in prose.

    HTML comments never render on the site, and in this corpus they are
    contributor notes -- "TODO: verify release after which the --cascade flag
    is switched", "UPDATE THIS WHEN PROMOTING TO BETA" -- plus Hugo section
    markers like `<!-- steps -->`. Left in, they became 103 chunks with no
    words at all and sat inside nearly a thousand more, where a reader saw
    them as documentation and a model could cite them as fact.
    """
    out: list[str] = []
    in_fence = False
    fence_marker = ""
    in_comment = False

    for line in text.splitlines():
        # Comments are resolved before fences, so a fence *inside* a comment
        # is commented out rather than opening a code block.
        if not in_fence and (in_comment or "<!--" in line):
            line, in_comment = _strip_html_comments(line, in_comment)

        stripped = line.lstrip()
        fence = _FENCE.match(stripped)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            out.append(line)
            continue

        if in_fence:
            out.append(line)
            continue

        line = _HEADING_SHORTCODE.sub(
            lambda m: _HEADING_LABELS.get(m.group(1).lower(), m.group(1).replace("_", " ").title()),
            line,
        )
        line = _GLOSSARY.sub(r"\1", line)
        line = _GLOSSARY_TERM_ONLY.sub(r"\1", line)
        line = _ADMONITION_OPEN.sub(lambda m: f"{m.group(1).capitalize()}:", line)
        line = _ADMONITION_CLOSE.sub("", line)
        line = _FEATURE_STATE.sub(
            lambda m: f"[Feature state: {m.group(2)}{f' in {m.group(1)}' if m.group(1) else ''}]",
            line,
        )
        line = _CODENEW.sub(lambda m: f"[Example manifest: {m.group(1)}]", line)
        line = _CAPTURE.sub("", line)
        line = _ANY_SHORTCODE.sub("", line)
        out.append(line)

    return "\n".join(out)


def _absolutize_links(text: str) -> str:
    return _RELATIVE_LINK.sub(lambda m: f"[{m.group(1)}](https://kubernetes.io{m.group(2)})", text)


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize_for_hash(text: str) -> str:
    """Canonical form for change detection.

    Whitespace-only edits must not look like a content change, or every
    reformatting commit upstream would trigger a full re-embed.
    """
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


def derive_url(source_path: str) -> str:
    """Map a repo path to its published docs URL.

    `concepts/workloads/pods/_index.md` -> `.../docs/concepts/workloads/pods/`
    """
    path = source_path[:-3] if source_path.endswith(".md") else source_path
    if path.endswith("_index"):
        path = path[: -len("_index")]
    path = path.strip("/")
    return f"{DOCS_BASE_URL}{path}/" if path else DOCS_BASE_URL


def first_heading(text: str) -> str:
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            return match.group(2).strip()
    return ""


def parse_markdown(raw: str, *, source_path: str, version: str) -> ParsedDocument:
    """Parse raw Markdown into clean text plus metadata."""
    try:
        post = frontmatter.loads(raw)
        meta_raw: dict[str, Any] = dict(post.metadata)
        body = post.content
    except Exception as exc:  # noqa: BLE001 - malformed front matter must not stop a run
        log.warning("parse.frontmatter_failed", source_path=source_path, error=str(exc))
        meta_raw, body = {}, raw

    body = _strip_shortcodes(body)
    body = _absolutize_links(body)
    body = _collapse_blank_lines(body)

    title = str(meta_raw.get("title") or "").strip() or first_heading(body) or source_path

    # Keep only front-matter fields that are small, stable, and useful as
    # retrieval metadata; the rest is Hugo build machinery.
    meta: dict[str, Any] = {
        str(key): meta_raw[key]
        for key in ("weight", "content_type", "description", "api_metadata", "no_list")
        if key in meta_raw and isinstance(meta_raw[key], (str, int, float, bool))
    }

    return ParsedDocument(
        source_path=source_path,
        version=version,
        title=title,
        url=derive_url(source_path),
        text=body,
        content_hash=content_hash(body),
        meta=meta,
    )


def parse_file(path: Path, *, root: Path, version: str) -> ParsedDocument | None:
    """Parse one file on disk. Returns None for files with no usable content."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("parse.unreadable", path=str(path), error=str(exc))
        return None

    source_path = path.relative_to(root).as_posix()
    parsed = parse_markdown(raw, source_path=source_path, version=version)
    if not parsed.text.strip():
        return None
    return parsed
