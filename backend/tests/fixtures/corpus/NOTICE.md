# Fixture corpus — attribution

The Markdown files under `1.26/` and `1.28/` are excerpts of the
[Kubernetes documentation](https://github.com/kubernetes/website), taken from
the `release-1.26` and `release-1.28` branches of `kubernetes/website`
(`content/en/docs/`).

Copyright belongs to the Kubernetes authors. Licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The files are
unmodified.

## Why they are committed

PROJECT_SPEC.md §6 requires a small committed corpus so that CI can run
ingestion and a retrieval evaluation without downloading ~750MB of tarballs on
every build.

The 30 pages were selected automatically for three properties:

1. present in **both** versions, so version-sensitive retrieval can be tested;
2. **materially different** between the two versions, so a version conflict is
   a real one rather than a contrived fixture;
3. rich in **code blocks and tables**, which are what chunking gets wrong.

The full corpus (three versions, ~3,100 pages) is fetched at ingestion time
into `data/raw/`, which is gitignored.
