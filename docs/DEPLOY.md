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

Plus an LLM key for generation (`.env.example`: option B, Groq, is the free one).

The Compose stack serves every config *except* the two that rerank
(`GT_SERVED_CONFIGS`), because choosing one loads a ~1 GB model (LIMITATIONS
L27). To offer them, size the server for them and list them in `.env`.

A public server answers whatever anyone asks, with your provider key. The
Compose stack therefore limits `/query` to 30 questions a minute across all
visitors (`GT_QUERY_RATE_PER_MINUTE`), runs two at a time
(`GT_MAX_CONCURRENT_QUERIES`; others wait up to 20 s, then get a 503), and
gives a stuck provider three attempts per call rather than five
(`GT_LLM_MAX_ATTEMPTS`; the client waits the provider's `Retry-After`). A full query makes five to ten model calls, so
check these against your provider's rate limit. The encoder loads at startup,
so `/health` reports `embedding_model_loaded: false` for the first few
seconds.

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
   branches. The evaluated subset, both chunk sets (`baseline` uses `fixed`;
   `full` and every other config share the `structure_aware` set):

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

## Google Cloud (Compute Engine)

The same Compose stack on one VM. Cloud Run plus Cloud SQL would also work,
but it costs more for a demo (Cloud SQL bills while idle), cold starts reload
torch on every wake-up, and ingestion would need a separate job; a VM keeps
every step above unchanged.

**Machine type.** The always-free `e2-micro` has 1 GB, below the backend's
floor. `e2-medium` (2 shared vCPUs, 4 GB) runs the stack including a rerank
config; `e2-standard-2` (8 GB) leaves headroom and embeds faster. Both are
billed; a new account's free-trial credit covers months of either. Check
current prices in the console before creating.

Run these from Cloud Shell, or anywhere with the `gcloud` CLI signed in.

1. **Create the VM**, with a disk large enough for the images (CPU torch is
   over a gigabyte) and the release tarballs:

   ```bash
   gcloud compute instances create groundtruth \
       --zone=us-central1-a --machine-type=e2-medium \
       --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
       --boot-disk-size=30GB --tags=groundtruth-web
   ```

2. **Open the port.** Unlike Oracle's images, Google's Ubuntu images have no
   host firewall to change; the VPC rule is the only one:

   ```bash
   gcloud compute firewall-rules create groundtruth-web \
       --allow=tcp:3000 --target-tags=groundtruth-web
   ```

   Behind a reverse proxy, allow `tcp:80,tcp:443` instead.

3. **Connect**, then follow steps 3-7 of the Compose section above (install
   Docker, clone, write `.env`, start, ingest, check):

   ```bash
   gcloud compute ssh groundtruth --zone=us-central1-a
   ```

4. **Keep the address.** The external IP is ephemeral and changes if the VM is
   stopped. To keep it, promote it to a static address:

   ```bash
   IP=$(gcloud compute instances describe groundtruth --zone=us-central1-a \
       --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
   gcloud compute addresses create groundtruth-ip --region=us-central1 --addresses=$IP
   ```

The site is then `http://<IP>:3000`. A stopped VM bills only for its disk and
any static address it holds.

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
