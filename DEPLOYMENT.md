# Atlas deployment and resume demo

Updated 18 September 2026. **The interactive app is now exposed through HTTPS:** [Open Atlas](https://cavalry-habitable-sureness.ngrok-free.dev). The source is at [shivam0870/atlas](https://github.com/shivam0870/atlas), and the static showcase is at [shivam0870.github.io/atlas](https://shivam0870.github.io/atlas/).

## Current ₹0 deployment

The real React frontend and FastAPI backend share one ngrok HTTPS origin. PostgreSQL, Redis, original files and local Qwen inference run on the existing Mac. The public instance has separate demo data and runtime configuration. The Mac must remain awake and online; the static showcase remains available independently.

**[Public demo startup, backup, configuration and verification](PUBLIC_DEMO.md)** is the operating guide for this instance. Public upload, indexing, streamed answers, citations, follow-ups, mobile evidence and refresh checks passed. Gmail SMTP authentication and signup sending passed; the owner's inbox verification click remains pending. Public reset/invitation email clicks and a restore of this new demo backup have not been verified.

No hosting purchase or paid model API was used. ngrok's free plan has usage limits and a browser welcome screen; it includes a provider-assigned HTTPS domain. [Current ngrok limits](https://ngrok.com/docs/pricing-limits/free-plan-limits). GitHub Pages serves the static project showcase. [GitHub Pages availability](https://docs.github.com/en/pages/getting-started-with-github-pages).

Source publication CI passed on product commit `86e87d6`: 89 backend tests (one model test deselected), 16 frontend tests and three real account/onboarding browser journeys. Public-native verification is separate from those hosted CI checks; see [release evidence](UPGRADE_RELEASE.md).

## Future dedicated-server architecture

Deploy the existing React frontend and FastAPI backend behind **one HTTPS address**. Atlas already serves `web/dist`; it does not need a separate frontend hosting service. Keep the frontend, `/api` and `/mcp` on the same origin to preserve the current cookie/session and CSRF design. Splitting providers is possible through an appropriate same-origin proxy, but is extra work for this release.

```mermaid
flowchart TD
    Visitor[Recruiter browser] --> HTTPS[Public HTTPS / Caddy]
    HTTPS --> Atlas[Atlas: React assets + FastAPI]
    Atlas --> DB[PostgreSQL + pgvector]
    Atlas --> Redis[Redis queue / admission]
    Atlas --> Cache[Separate Redis cache]
    Atlas --> Model[Ollama / local Qwen]
    Redis --> Worker[Indexing and evaluation worker]
    Worker --> DB
    Worker --> Model
    Atlas --> Files[Persistent uploaded files]
    Worker --> Files
    Atlas --> Email[Real SMTP delivery]
```

### What is needed

| Item | Why / decision |
|---|---|
| GitHub repository | Source and evidence link for the resume; published at `shivam0870/atlas` |
| Linux server or existing host | Runs the API, worker, database, Redis and local inference continuously |
| Initial sizing | **Planning estimate: 4–8 vCPU, 16 GiB RAM, at least 80 GB SSD**, one generation at a time; benchmark on the actual host before promising latency/capacity |
| Domain or subdomain | Stable HTTPS URL and account email links; Caddy terminates TLS |
| SMTP provider and verified sender | Public users need verification/reset/invitation emails; local Mailpit is for development |
| Persistent storage | Database, queue, original uploads, model files and TLS state survive restarts |
| Backups | Database + uploads + matching MFA encryption key, stored privately off the server |
| Synthetic demo corpus | A useful initial company with documents and service inventory; avoid publishing local personal data/test inboxes |

Paid model API usage remains disabled. **Zero model API cost does not mean zero hosting cost.** A rented server, domain, backups and SMTP may have costs. No paid resources should be created until the actual quote and budget are agreed.

## Hosting choices

1. **Always-on full app:** use one sufficiently provisioned server for a small resume demo. This avoids separate database/worker/inference subscriptions. CPU generation needs a real latency test; GPU hosting is a later option if required.
2. **No hosting spend:** publish the source, screenshots and a recorded walkthrough on a public project page; demonstrate the native app during interviews. An optional tunnel can expose a dedicated demo instance while the computer is awake, but that is not a 24/7 deployment.
3. **Existing cloud credits/server:** use available capacity after checking RAM, persistent disk, SMTP egress and actual credit expiry/costs. Do not assume a free-tier machine can run Qwen plus the rest of Atlas.

Provider facts checked on 18 September 2026:

- Render free web services have limited compute and idle behavior; persistent disks require paid services. That does not cover Atlas's durable uploads and separate long-running worker for free. [Render disks](https://render.com/docs/disks), [Render free services](https://render.com/docs/free).
- Cloudflare **Quick Tunnels do not support SSE**, which Atlas uses to stream answers. Do not use a random `trycloudflare.com` Quick Tunnel for the complete demo. A configured Cloudflare Tunnel is a different option. [Cloudflare limitations](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
- ngrok's free offering has request/transfer limits and a browser warning page. It is a candidate for supervised demonstrations, subject to an actual streamed-answer test. [ngrok limits](https://ngrok.com/docs/pricing-limits/free-plan-limits).
- Check current server pricing and availability at purchase time, including region, tax, IPv4 and backups. [Hetzner pricing adjustments](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/). No provider price or availability is promised here.

## Deployment files prepared

- [deploy/compose.yaml](deploy/compose.yaml): separate `atlas-public` project; only the HTTPS proxy publishes ports; API/worker share persistent originals; separate application/identity/worker credentials; migration-only administrative credentials; real SMTP settings; local models only.
- [deploy/Caddyfile](deploy/Caddyfile): one public origin with streaming-capable reverse proxy. Caddy handles SSE without ordinary response buffering. [Caddy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).
- [deploy/.env.example](deploy/.env.example): required private configuration. `deploy/.env` is ignored by Git and the root `.env` stays unchanged.

These files are a candidate for **a new dedicated demo server**, not an instruction to publish this laptop or point production at its development database. The earlier shared 2 GiB Colima VM did not complete container inference; see [release evidence](UPGRADE_RELEASE.md). Full public rollout must pass the checks below on the actual server.

### Preparation checks actually run

- Docker Compose resolved all 10 services using disposable synthetic configuration. Checked that only the proxy publishes ports, API and worker share the uploads volume, runtime processes do not receive migration credentials, and HTTPS/SMTP/zero paid budget settings are applied.
- Caddy 2.11.4 validated the actual Caddyfile in an isolated container with networking disabled and no published ports: **valid configuration**. This did not issue a public certificate or expose a service.
- Confirmed `deploy/.env` is Git-ignored and excluded from Docker build context; whitespace checks passed.
- Full server startup, public TLS/email, streamed cloud inference and cloud backup recovery have **not** run. Existing local backend/browser evidence is recorded separately in the release report.

### Before running on the chosen server

1. Select the provider/budget and give access through its normal authentication flow. Never paste passwords, private SSH keys or API tokens into chat.
2. Select a GitHub repository, public hostname, SMTP sender/provider and a small synthetic demo dataset. The deployment has no shared public owner password. Visitors can register and create personal workspaces; a seeded demo company can be accessed through scoped invitations for guided reviews.
3. Install Docker/Compose on the dedicated Linux host and build for its architecture. A locally built Apple Silicon image is not an amd64 server image.
4. Copy `deploy/.env.example` to private `deploy/.env` with mode 0600. Generate four independent hexadecimal database passwords and a Fernet key; preserve them. Fill in actual SMTP settings and the hostname. SMTP is STARTTLS, usually port 587. The hostname must match DNS and the HTTPS URL.
5. Point DNS at the server. Expose 80/443; restrict SSH. Keep database, Redis, model and administrative endpoints private. Do not publish Mailpit.

### Candidate launch commands

Run from the repository root **on the selected dedicated server**, after its configuration is complete:

```sh
docker build -t atlas:resume-20260918 .
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d postgres redis cache ollama
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm migrate
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm models
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm model-pull
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d api worker proxy
```

Use an immutable image digest for subsequent published releases. Pin downloaded model digests against the repository's model manifests before claiming reproducibility. Do not print resolved Compose configuration with real secrets; use `config --quiet`.

The production candidate intentionally disables telemetry export until a collector is configured. `/ready` checks database and queue connectivity; it does not prove inference works. Keep a separate actual-question smoke check and worker indexing check.

### Public acceptance checks

1. HTTPS and direct/deep links work from a different device/network.
2. Real email signup, verification, login, invitation and password reset work at the public URL; Secure cookies and CSRF checks work behind the proxy.
3. Upload PDF/DOCX → worker indexing → streamed question/answer → correct citation → follow-up → browser refresh works.
4. Two companies and a restricted space remain isolated; revoke a grant and verify old answers/saved items/source downloads become unavailable.
5. API/worker/server restart preserves users, MFA, uploads and conversation/version references.
6. Check disk/RAM usage, first-answer latency and behavior under the intended small demo concurrency. Tune quotas and admission before sharing a public registration link.
7. Verify backup restoration on the actual deployment layout before calling the hosted release ready.

The existing `scripts/backup.py` and `restore_backup.py` are deliberately bound to the **local** `atlas-rag-postgres-1` / port 55432 setup. They are not remote production backup scripts. A production backup procedure must use this Compose project's PostgreSQL service and named uploads volume, retain the matching private configuration, stop writers for a consistent release snapshot, and rehearse restoration into a separate target. Do not run `down -v` as an upgrade or rollback.

## Resume deliverables

You can describe the completed local engineering work now, while being accurate about deployment status. Add a public URL only after it passes the hosted checks.

**Suggested project title:** Atlas — Multi-tenant AI Knowledge Platform

**Suggested resume bullets:**

- Built a multi-tenant knowledge platform with React, FastAPI, PostgreSQL/pgvector and Redis, supporting role- and team-based access, private conversations and versioned PDF/DOCX ingestion.
- Implemented hybrid vector/BM25 retrieval, citation-backed local-model answers, agent/MCP tools and permission revalidation across caches, historical answers and source downloads, with no paid model API usage.
- Verified account/MFA security, tenant isolation and document-to-answer workflows with 90 backend tests, 16 frontend tests and 7 real browser journeys; rehearsed existing-data migrations and backup recovery.

Include a repository link, architecture diagram, screenshots, a short narrated demo, executed test results and clear setup instructions. Do not claim a human-reviewed accuracy percentage, production scale, Kubernetes deployment or completed public hosting until those are actually verified.

## Current handoff

The source, static showcase and interactive ngrok endpoint are public. The endpoint uses the existing Mac and depends on its uptime. Public document-to-answer checks passed; inbox verification and public reset/invitation email clicks remain pending. See [PUBLIC_DEMO.md](PUBLIC_DEMO.md) for the exact operating procedure and limits. The Docker/Caddy files above remain candidates for a future sufficiently provisioned server.
