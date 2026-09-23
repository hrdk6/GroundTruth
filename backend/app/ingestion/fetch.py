"""Fetch the Kubernetes documentation for a release branch.

Downloads the branch tarball from GitHub and extracts only `content/en/docs/`.
A tarball beats `git clone` here: no git history (the repo is large), one HTTP
request per version, and the archive is cached on disk so re-ingesting is
offline and instant.

Corpus licensing: the Kubernetes documentation is CC BY 4.0. Nothing fetched
here is committed -- `data/raw/` is gitignored.
"""

from __future__ import annotations

import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.logging import get_logger
from app.core.settings import REPO_ROOT

log = get_logger(__name__)

RAW_DIR = REPO_ROOT / "data" / "raw"
ARCHIVE_DIR = REPO_ROOT / "data" / "archives"

REPO = "kubernetes/website"
DOCS_PREFIX = "content/en/docs/"

# Release branch per corpus version, per PROJECT_SPEC.md S6.
VERSION_BRANCHES: dict[str, str] = {
    "1.26": "release-1.26",
    "1.28": "release-1.28",
    "1.30": "release-1.30",
}
DEFAULT_VERSIONS = tuple(VERSION_BRANCHES)


@dataclass(frozen=True)
class FetchResult:
    version: str
    branch: str
    root: Path
    file_count: int
    from_cache: bool


def latest_version(versions: tuple[str, ...] | list[str] = DEFAULT_VERSIONS) -> str:
    """Newest version by numeric order, not string order ('1.9' < '1.10')."""
    return max(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


def _archive_path(branch: str) -> Path:
    return ARCHIVE_DIR / f"{branch}.tar.gz"


def download_branch(version: str, *, force: bool = False, timeout: float = 300.0) -> Path:
    """Download a branch tarball, reusing the cached archive when present."""
    branch = VERSION_BRANCHES[version]
    archive = _archive_path(branch)
    if archive.exists() and not force:
        log.info("fetch.archive_cached", version=version, path=str(archive))
        return archive

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{branch}"
    log.info("fetch.downloading", version=version, branch=branch, url=url)

    tmp = archive.with_suffix(".tmp")
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
        response.raise_for_status()
        with tmp.open("wb") as fh:
            for block in response.iter_bytes(chunk_size=1 << 20):
                fh.write(block)
    tmp.replace(archive)

    log.info("fetch.downloaded", version=version, bytes=archive.stat().st_size)
    return archive


def extract_docs(version: str, *, force: bool = False) -> FetchResult:
    """Extract `content/en/docs/` from the cached tarball into `data/raw/{version}/`."""
    branch = VERSION_BRANCHES[version]
    target = RAW_DIR / version

    if target.exists() and not force:
        count = sum(1 for _ in target.rglob("*.md"))
        if count:
            log.info("fetch.extract_cached", version=version, files=count)
            return FetchResult(version, branch, target, count, from_cache=True)

    archive = download_branch(version, force=force)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    count = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            # Archive members are prefixed with "website-release-1.28/".
            parts = member.name.split("/", 1)
            if len(parts) != 2:
                continue
            relative = parts[1]
            if not relative.startswith(DOCS_PREFIX) or not relative.endswith(".md"):
                continue

            inner = relative[len(DOCS_PREFIX) :]
            destination = target / inner
            # Refuse anything that would escape the target directory: tar
            # members are untrusted input even from a known host.
            if not destination.resolve().is_relative_to(target.resolve()):
                log.warning("fetch.skipped_unsafe_member", name=member.name)
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, destination.open("wb") as fh:
                shutil.copyfileobj(source, fh)
            count += 1

    log.info("fetch.extracted", version=version, files=count, root=str(target))
    return FetchResult(version, branch, target, count, from_cache=False)


def fetch_versions(
    versions: tuple[str, ...] | list[str] = DEFAULT_VERSIONS, *, force: bool = False
) -> list[FetchResult]:
    unknown = set(versions) - set(VERSION_BRANCHES)
    if unknown:
        raise ValueError(f"Unknown versions {sorted(unknown)}; known: {sorted(VERSION_BRANCHES)}")
    return [extract_docs(version, force=force) for version in versions]


def iter_markdown(root: Path) -> list[Path]:
    """Every Markdown file under `root`, in stable order.

    `_index.md` files are Hugo section landing pages and are kept: they often
    hold the conceptual overview a question is really about.
    """
    return sorted(p for p in root.rglob("*.md") if p.is_file())
