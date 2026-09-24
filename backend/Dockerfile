# Backend image. Build from the REPO ROOT, not backend/:
#
#     docker build -f backend/Dockerfile .
#
# The API reads configs/ and experiments/, which live beside backend/. The image
# mirrors the repo layout under /srv so `REPO_ROOT` (app/core/settings.py)
# resolves to /srv exactly as it resolves to the checkout on a laptop. Built
# from backend/ alone, both directories were missing and every /query was a 404.
#
# Python 3.12 (not 3.13/3.14) because that is the version with reliable torch /
# sentence-transformers wheels.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

# Dependency layer first: it changes far less often than the source. Installed
# from the lockfile, which pins the CPU-only torch wheel for both x86_64 and
# aarch64, and without the `dev` extra (pytest, mypy, pgserver).
WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-install-project

COPY backend/ ./
COPY configs/ /srv/configs/
COPY experiments/ /srv/experiments/

# Bake the embedding model into the image. Without this every cold start
# downloads it from the Hugging Face Hub before the first query can run, and a
# host with no persistent disk (most free tiers) does that on every wake-up.
# The model name comes from the served config, not from this file.
ARG GT_DEFAULT_CONFIG=full
RUN python -c "from app.core.pipeline import load_config; \
from app.ingestion.embed import get_embedder; \
get_embedder(load_config('${GT_DEFAULT_CONFIG}').embedding).embed_query('warm-up')"

# Everything the process writes -- the LLM cache (/srv/.cache), a corpus
# fetched for ingestion (/srv/data), models (/opt/hf) -- is created here and
# owned by the runtime user. A volume mounted over a path the image lacks comes
# up owned by root, and the first write fails.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /srv/.cache /srv/data \
    && chown -R app:app /srv/.cache /srv/data /opt/hf
USER app

EXPOSE 8000
# Migrations run on every start: they are idempotent, the image is the unit of
# deployment, and a free host has no separate release step to run them in. One
# uvicorn worker, because each worker loads its own copy of the model. PORT is
# set by hosts such as Render; 8000 otherwise.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
