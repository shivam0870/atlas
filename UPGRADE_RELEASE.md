# Atlas F01–F14 release verification

## Delivered product

Atlas is now a private knowledge workspace for individuals and companies. A person registers and verifies an account, creates or joins a company, organizes permitted knowledge, uploads documents, and asks questions with persistent conversations and versioned evidence. Company owners manage people, teams, access, integrations, inventory, usage and evaluations.

The release uses local Qwen generation and local retrieval models. Paid model providers are disabled and the configured external model budget is zero. Development email is delivered to the local Mailpit inbox. No production deployment or Git push was performed.

**Status:** F01–F13 implemented and verified locally. F14 implementation is present; native delivery, migrations, recovery, image build and packaged identity/UI checks passed, while container inference acceptance remains partial. Implementation is in this working tree. The native application is the primary verified local runtime; unavailable checks are not treated as passes.

## Feature acceptance

| ID | Implemented outcome | Executed verification |
|---|---|---|
| F01 | Registration, verification, login/logout, password and email recovery, profile | Account PostgreSQL/Mailpit tests and real browser registration/recovery |
| F02 | Hashed opaque sessions, idle/absolute expiry, revocation, CSRF, encrypted TOTP, one-use recovery codes, reauthentication | Session/reset/MFA/replay tests; browser MFA and reset invalidate old sessions |
| F03 | Company/personal onboarding, membership-only switcher, first-use checklist, safe local legacy ownership claim | Organization tests and browser company creation/switching |
| F04 | Invitations, four roles, teams, suspension/removal, leave/transfer, last-owner protection | Organization concurrency/permission tests; real invitation and verified-account inbox journeys |
| F05 | Company/restricted spaces, narrowing document grants, service grants, forced row-level security | Direct SQL and API permission tests; cached/history/saved/citation/evaluation/MCP access checks |
| F06 | Bulk TXT/MD/PDF/DOCX ingestion, library filters/actions, immutable versions, review dates, archive/trash/purge | Real upload/worker/PDF/DOCX/download/replacement journey plus document/lifecycle regressions |
| F07 | Private persistent conversations, follow-ups/scopes, stop/retry/regenerate, pagination and complete authorized export | Real model browser journey; 503-message pagination/export and source-revocation regressions |
| F08 | Inline citations and source panel, version/page references, summaries, feedback, honest generation states | Browser answer/evidence/mobile journey; versioned source and feedback regressions |
| F09 | Search, command menu, bookmarks/saved answers, useful home and notification inbox | Discovery tests, browser save/search, invitation/index/review notification checks |
| F10 | Service/deployment CRUD, CSV preview/import, comparison and supporting evidence | Inventory browser journey and actual local-model agent/MCP calls |
| F11 | Company/people/access/integration settings, scoped credentials, quotas, audit and deletion lifecycle | Administration browser/API tests; key revoke, accounting, member limits and durable erasure |
| F12 | Label review, feedback triage, background evaluation/cancellation, comparison, usage and readiness | Evaluation/administration tests plus an actual durable-worker/local-model evaluation; human corpus-quality approval remains pending |
| F13 | Forest-green design, real URLs, light/dark/system, responsive navigation, accessible controls, typed scoped fetching | 16 frontend tests, production build/typecheck/format; Chrome desktop/mobile/keyboard journeys |
| F14 | Additive migrations 006–015, legacy migration/claim, isolated fixtures, backup/recovery tools and package builds | Fresh/upgrade checks, 90 backend tests, real browser/model/tool checks; native/recovery and packaged identity checks passed; **container inference acceptance remains partial** |

See [UPGRADE_EXECUTION.md](UPGRADE_EXECUTION.md) for ownership and dependencies, and [UPGRADE_CONTRACTS.md](UPGRADE_CONTRACTS.md) for shared implementation contracts.

## Security and data behavior

- Browser users and integration service identities are separate. Every request binds a live principal, selected company and session/key to transaction-local PostgreSQL context. The application role cannot bypass forced row-level security.
- Retrieval filters authorized source IDs before vector/BM25 work. Source reads, downloads, tools, evaluation evidence and persisted answers recheck current access. Cached answers include identity/access context and revalidate their evidence. Losing access also hides old answer text and agent diagnostics that depend on the removed source.
- Owners/admins administer company-owned knowledge. They cannot automatically open another person's private conversations or saved answers. Legacy queries with unknown human authors remain in an administrator-only archive.
- Source references bind document, immutable version and company together. A replacement publishes atomically; historical citations continue to refer to the original version. Explicit permanent deletion erases dependent answers, evaluation results, indexes and files through an idempotent queue.
- Password reset invalidates existing sessions. TOTP and recovery codes reject replay. Ownership and destructive account actions require reauthentication.
- Company deletion disables access immediately. A worker purges deleted companies after 30 days and anonymizes disabled personal accounts after the retention period. Company-owned records remain with an active company. Backup retention is managed separately by the operator.

## Executed checks

| Check | Result / evidence |
|---|---|
| Final complete backend suite | **90 passed in 40.16 s**, including real local embedding regression — [JUnit](artifacts/upgrade/backend-tests.xml) |
| Frontend components/API state | **16 passed**; TypeScript, production build and formatting passed |
| Real Chrome browser suite | **7 passed in 52.3 s**, zero skipped/flaky/unexpected; see [sanitized summary](artifacts/upgrade/browser-summary.json) |
| PDF/DOCX actual HTTP and worker journey | Passed in 9.1 s — [evidence](artifacts/upgrade/upload-journey.json) |
| Real MCP SDK | Passed for both preserved companies and three tools — [evidence](artifacts/mcp-smoke.json) |
| Actual local-model tool agent | Passed in 99.082 s, inventory plus document evidence, $0 API spend — [evidence](artifacts/agent-smoke.json) |
| Actual durable evaluation | Queued → running → completed in 58.2 s; real retrieval/generation/judge, 599 input / 131 output tokens, $0 API cost — [synthetic-fixture evidence](artifacts/upgrade/evaluation-journey.json) |
| Unavailable primary → actual llama.cpp fallback | Passed in 5.803 s, $0 API spend — [evidence](artifacts/failover-smoke.json) |
| Fresh schema | Migration 015, 39 tables, zero tenants — [evidence](artifacts/upgrade/fresh-install.json) |
| Existing-data upgrade | Original snapshot restored into a separate database; 2 tenants, 118 documents, 3,590 chunks/embeddings and 20 queries preserved exactly; zero unversioned chunks — [evidence](artifacts/upgrade/migration.json) |
| Post-upgrade recovery | Separate database restored; 11 table counts, 15 original file hashes and 5 encrypted MFA enrollments verified — [evidence](artifacts/upgrade/recovery.json) |
| Packaged personal-account journey | Passed with UID 10001 and exact final UI assets — [evidence](artifacts/upgrade/container-accounts.json) |
| Python static checks | Ruff check/format passed for 95 files; mypy passed for 28 source modules; `git diff --check` passed |

Backend tests use real PostgreSQL, Redis and Mailpit; selected model-boundary unit/integration tests stub generation intentionally. The Chrome knowledge journey, separate tool-agent/fallback checks, durable evaluation and embedding regression use actual local models. The evaluation rehearsal uses 40 automated approval fixtures in a disposable isolated database to pass the review gate and selects one question; these fields do not represent human review. The PDF/DOCX journey verifies extraction/indexing/download/version behavior and makes no generation call. A passing mocked test is not used as evidence of actual model inference.

One added owner-filter browser assertion initially timed out because its test selector was incorrect. It was changed to the actual accessible combobox role with an explicit option check; the requirement and production code were unchanged. The final complete run passed.

The browser journeys cover account verification, invitation acceptance, MFA/recovery replay, password-reset invalidation, roles, source revocation, company switching, upload/index/question/citation/follow-up/refresh, saved content, inventory/administration, mobile layouts and keyboard navigation. Screenshots contain synthetic demonstration data:

- [Home](artifacts/atlas-upgrade-home.png)
- [Answer](artifacts/atlas-upgrade-answer.png) and [evidence](artifacts/atlas-upgrade-evidence.png)
- [Dark account settings](artifacts/atlas-upgrade-account-dark.png)
- [Mobile](artifacts/atlas-upgrade-mobile.png)

### Packaging and recovery results

The final prebuilt-assets image `atlas-rag:upgrade` (`3294fb897ce3`) built successfully. Its API runs as UID 10001 and passed health/readiness, exact final UI asset/deep-route, registration, Mailpit verification, personal login, company onboarding, default-space and logout checks on port 8101. [Container account evidence](artifacts/upgrade/container-accounts.json). The uncached query was attempted with a 448 MiB container limit in the shared 2 GiB VM. Shared services became slow and native readiness timed out, so the lead stopped only the verification container. The query connection ended incomplete; exit 137 followed the explicit stop, with `OOMKilled=false`. Native readiness recovered afterward. **Container inference is unverified; F14 acceptance is partial on this deployment path.** [Container evidence](artifacts/container-smoke.json).

A 17.35 MB post-upgrade backup was restored into a new database on migration 015. All 15 uploaded originals matched their hashes, all 5 existing MFA secrets decrypted with the backed-up key, and 11 table counts matched the backup-time snapshot. The recovery connection file has mode 0600 and uses Redis database 14. New source companies created after the snapshot were excluded from the timestamp comparison; no running database was replaced. [Recovery evidence](artifacts/upgrade/recovery.json).

An earlier full frontend compiler image build succeeded, but a later rebuild in the shared 2 GiB Colima VM stalled and was explicitly stopped by the lead. Unrelated services stayed running. `Dockerfile.prebuilt` packages the host-verified `web/dist` bundle and avoids that compiler memory peak. Both this packaged uncached-query path and full container-only generation remain unverified; the native local application is fully exercised.

## Use locally

From the repository root:

```sh
python3 scripts/local.py start
python3 scripts/local.py status
```

Open **http://127.0.0.1:8100**. Register an account, open **http://127.0.0.1:58025** for the verification email, then create a company or personal workspace. Mailpit receives local verification, invitation and recovery messages; these checks do not establish external email delivery.

For the existing Acme/demo company, first register and verify your own email, then explicitly bind that verified account as owner with local administrative credentials:

```sh
.venv/bin/python scripts/claim_workspace.py --tenant acme --email your-verified-address@example.test
```

Replace the email and tenant slug with the intended values. Ordinary signup cannot claim an existing company. Claiming revokes its legacy keys; create replacement service credentials in **Integrations** with the necessary spaces/scopes. No shared human password is supplied.

For a clean machine, prerequisites and `make bootstrap` are documented in [README.md](README.md#clean-setup). The verified host is Apple M1 Pro/16 GiB with native Metal generation, and Docker PostgreSQL/Redis/Mailpit. Network access is needed to install dependencies and download models initially; no paid model API is required. Stop the project's native processes with `python3 scripts/local.py stop`; unrelated local Docker services are not stopped.

### Reproduce verification

```sh
# Creates separate databases; never targets the daily-use database.
.venv/bin/python scripts/verify_upgrade.py --fresh
.venv/bin/python scripts/verify_upgrade.py
.venv/bin/python scripts/isolated.py .venv/bin/pytest -q

npm --prefix web run test:unit
npm --prefix web run build
npm --prefix web run format:check
npm --prefix web run test:e2e

.venv/bin/python scripts/verify_upload_journey.py
.venv/bin/python scripts/mcp_smoke.py
.venv/bin/python scripts/agent_smoke.py
.venv/bin/python scripts/isolated.py .venv/bin/python scripts/verify_evaluation_journey.py
```

`verify_upgrade.py` uses the private pre-upgrade snapshot identified by `.local/latest-upgrade-backup` and writes `.local/upgrade-test.env`. Backend fixtures use isolated Redis database 15. Browser and HTTP journey scripts create synthetic accounts/content in the running local application; they require Mailpit, the API/worker and local models. MCP/agent checks assume the unclaimed seeded Acme/Globex legacy service keys; after claiming, configure replacement scoped service credentials before adapting those fixture scripts. Failover verification additionally needs a real llama.cpp server; the temporary verification server was stopped after its successful run.

## Migrations, backup and recovery

Do not reset the database or run down-migrations as a recovery method. Existing content was tested through additive migration 015. Before upgrading another installation:

```sh
python3 scripts/local.py stop
.venv/bin/python scripts/backup.py
python3 scripts/configure.py
uv sync --frozen
.venv/bin/alembic upgrade head
npm --prefix web ci
npm --prefix web run build
python3 scripts/local.py start
```

The private backup bundle contains `atlas.dump`, `uploads/`, `configuration.env` and a manifest. Protect this bundle: it contains account data and secrets. Keep the original `MFA_ENCRYPTION_KEY` with the database; replacing it breaks existing authenticator enrollment. Stop all writers, including a container worker if using containers, before a release backup.

Rehearse recovery into a **new** database:

```sh
.venv/bin/python scripts/restore_backup.py .local/backups/atlas-YYYYMMDDTHHMMSS --database atlas_recovery_review
```

The command refuses an existing target, restores the dump, applies current migrations and writes private connection overrides. It leaves the running database intact. Verify the restored target with isolated Redis queues and matching copied uploads/configuration. For deliberate cutover, stop writers, switch all four database URLs together, restore matching uploads and the MFA key, and validate login, original downloads, historical citations and indexing before serving users. Retain the original database and bundle until recovery is verified. See [README.md](README.md#product-upgrade-existing-data-and-recovery).

## Remaining verification and release boundaries

### Public source and showcase — 18 September 2026

Source and release artifacts are published at [shivam0870/atlas](https://github.com/shivam0870/atlas), with the static project showcase at [shivam0870.github.io/atlas](https://shivam0870.github.io/atlas/). The native interactive app is now available through [the ngrok HTTPS endpoint](https://cavalry-habitable-sureness.ngrok-free.dev) while the Mac is online.

[GitHub CI run 35369073611](https://github.com/shivam0870/atlas/actions/runs/35369073611) passed for product commit `86e87d6`: Ruff/type checks, formatting, frontend production build, **89 backend tests (1 model test deselected)**, **16 frontend tests**, fresh database migrations and **3 real Chrome account/onboarding journeys**. These remote browser checks are a subset of the seven local browser journeys above; hosted CI did not run local-model inference or the full document-to-answer journey. The separate corpus workflow correctly skipped retrieval evaluation because the human-reviewed baseline is not accepted.

GitHub Pages deployment succeeded. Chrome checks against the actual public HTTPS page passed at 1440px and 390px: HTTP 200, screenshot loading, no horizontal overflow, keyboard skip link, section navigation, disclosure controls and no page errors. This verifies the static showcase, not a hosted Atlas API.

### Public native demo verification

The isolated public instance passed actual HTTPS login/session/CSRF checks, PDF/DOCX upload and indexing, original downloads, company isolation, document replacement with preserved historical versions, and a Chrome journey covering UI upload, two local-model answers, citations, saving, follow-up, refresh and mobile evidence. See [sanitized public evidence](artifacts/public-demo.json) and [operation instructions](PUBLIC_DEMO.md). These tests use a separate locally provisioned account and do not claim an email-verification pass for it.

Gmail certificate verification and authentication passed; an actual public registration sent a verification email accepted by SMTP. Inbox delivery/link completion remains pending owner confirmation. The actual signup account remains unverified until that link is used. No inbox was read. The SMTP adapter now explicitly validates server certificates. Seven isolated account regression tests and Python static checks passed after that change.

A consistent private backup of the dedicated demo database, originals and matching configuration was created with the demo stopped; file hashes and private configuration permissions passed. The dedicated API, worker and tunnel restarted successfully. Restore of this new public instance has not been rehearsed.

### Still pending

- Human-reviewed corpus quality is still pending owner review of the labels. The evaluation and review features work; existing synthetic/candidate results do not establish an approved production retrieval-quality score.
- Always-on cloud production hosting, public inbox/reset/invitation link completion, a Kubernetes cluster deployment, formal screen-reader audit and production-scale load were not verified. The current public demo depends on the Mac's uptime. Historical load metrics in README/TEST_REPORT predate this product upgrade.
- **F14 partial:** container inference needs a separately verified resource allocation; native delivery, migrations, recovery, image build and packaged account/UI flows passed. The shared reference Colima VM has 2 GiB and also runs unrelated services; those services were preserved. Final packaging evidence appears above.
- Deferred proposal items, including OCR, enterprise SSO and public/shared conversations, remain outside this release.
- The dedicated demo has its actual HTTPS `APP_URL`, SMTP credentials and protected persistent storage, separate from the daily-use local app. Any future cloud deployment needs its own configuration and verification. All model API spend remains zero.
