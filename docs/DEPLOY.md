# Deploying GroundTruth

Three processes: Postgres 16 with pgvector, the FastAPI backend, and the
Next.js frontend. The browser only talks to the frontend; `/api/*` is proxied
to the backend, so the API needs no public port of its own. Both images are
built by CI (`docker` job) and the stack is driven through that proxy on every
push. The job summary reports the containers' memory on Linux.

## What it needs

Measured on the dev machine (Windows, psutil) on 24 Sep 2026; figures on
Linux differ somewhat, and the CI job summary has those.

| | Memory | Notes |
|---|---|---|
| Backend, after one `full` query | **513 MB** private, 584 MB peak | torch + bge-small resident; one uvicorn worker |
| Backend, a `hybrid_rerank*` config chosen | **1,637 MB** private | + bge-reranker-base, loaded on first use |
| Frontend (standalone server) | ~75 MB | |
| Postgres data, evaluated subset | 111 MB on disk | 639 pages, both chunk sets (11,059 chunks) |

Plus an LLM key for generation (`.env.example`, option A or B).

The rerank configs are selectable from the Ask page, so size a server for
them or expect the process to be killed when a visitor picks one (LIMITATIONS
L27).

## One server with Docker Compose (recommended)

`docker-compose.prod.yml` runs all three. It works on x86_64 and on ARM: the
lockfile pins CPU-only torch wheels for both, and every base image is
multi-arch.

On **Oracle Cloud's Always Free tier**, use an Ampere A1 instance
(`VM.Standard.A1.Flex`, the whole free allowance: 2 OCPUs, 12 GB). The
`E2.1.Micro` shape has 1 GB and 1/8 OCPU, which is below what the backend
alone needs.

1. **Create the VM** (Ubuntu 24.04). If the console reports *out of host
   capacity*, try another availability domain or try again later.

2. **Open the port.** Two firewalls, and both must allow it: an ingress rule on
   the subnet's security list (TCP 3000, or 80/443 behind a reverse proxy), and
   the instance's own iptables, which Oracle's Ubuntu images ship closed except
   for SSH:

   ```bash
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 3000 -j ACCEPT
   sudo netfilter-persistent save
   ```

3. **Install Docker Engine and the Compose plugin** following
   <https://docs.docker.com/engine/install/ubuntu/>.

4. **Configure.** Clone the repo and create `.env` beside the Compose file:

   ```bash
   git clone https://github.com/hrdk6/GroundTruth.git && cd GroundTruth
   cp .env.example .env
   ```

   Set `POSTGRES_PASSWORD` (required), the LLM provider variables, and
   `GT_LLM_COST_PER_MTOK_IN/OUT` if the provider is paid. Leave
   `GT_ADMIN_TOKEN` unset unless `POST /ingest` should be reachable.

5. **Start.** The backend migrates the database on every start.

   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

6. **Ingest.** The corpus is fetched from the kubernetes/website release
   branches. The evaluated subset, both chunk sets (`full` serves
   `structure_aware`; `baseline` and the plain `hybrid` configs use `fixed`):

   ```bash
   docker compose -f docker-compose.prod.yml run --rm backend \
       python -m app.ingestion.run --config ../configs/full.yaml \
       --versions 1.26 1.30 --include concepts tasks
   docker compose -f docker-compose.prod.yml run --rm backend \
       python -m app.ingestion.run --config ../configs/baseline.yaml \
       --versions 1.26 1.30 --include concepts tasks --no-fetch
   ```

   Embedding is CPU-bound; on the dev machine each took about six minutes.

7. **Check.** `curl localhost:3000/api/health` should report `"status": "ok"`
   with `pgvector: true`, then ask a question on `http://<ip>:3000`.

For HTTPS, put a reverse proxy such as Caddy in front of port 3000 and open
80/443 instead.

**Idle reclamation.** Oracle reclaims an Always Free instance that, over seven
days, stays under 20% on 95th-percentile CPU, on network, and (A1 only) on
memory. A demo that nobody visits for a week fits all three.

## Render

Deployable, but not on a 512 MB instance: the backend's resident floor is at
or above 512 MB before it serves a request (the table above), and the free
tier's 0.1 CPU makes loading torch on every wake-up slow. It needs at least
the 2 GB instance for the backend. The frontend fits in 512 MB.

- **Backend:** a Docker web service, Dockerfile `backend/Dockerfile`, build
  context the repo root. Set `DATABASE_URL` to the database's internal URL
  (the `postgres://` form works as given) plus the LLM variables. `PORT` is
  honoured.
- **Frontend:** a Docker web service, Dockerfile `frontend/Dockerfile`, context
  `frontend`, with `NEXT_PUBLIC_API_URL` set to the backend's URL. It is read
  at build time, so a changed backend URL needs a rebuild.
- **Database:** Render Postgres supports pgvector. The free database has 1 GB
  and expires 30 days after creation.
