# Atlas upgrade execution tracker

Approved scope: F01–F14 on 17 September 2026. Base commit: `c191a71`. The original untracked proposal is preserved. No applicable AGENTS.md was found. No commits or pushes were performed.

## Coordination and ownership

Root coordinated three real workers within the four-slot environment limit. Shared working tree ownership prevented concurrent edits. Root owned migrations 006–015, dependency manifests/locks, database context, central configuration, integration and release verification. Contract changes were agreed in [UPGRADE_CONTRACTS.md](UPGRADE_CONTRACTS.md). Agents were reassigned after dependent work became available; model-heavy checks ran serially.

| Feature | Owner | Dependencies | Acceptance criteria / executed evidence | Status |
|---|---|---|---|---|
| F01 Accounts | identity + frontend | 006, identity pool, Mailpit | Signup/verify/profile/password/email recovery, token replay; real browser + PostgreSQL tests | Implemented and verified |
| F02 Sessions/MFA | identity; root review | F01, encryption key | Reset/revoke/CSRF/reauth/MFA/recovery replay; real browser + PostgreSQL tests | Implemented and verified |
| F03 Onboarding | identity + frontend | F01, default spaces | Atomic/idempotent create, personal/company, safe claim, switching | Implemented and verified |
| F04 People/roles/teams | identity + frontend | F03 | Matching-email invitations, role/team limits, last-owner concurrency | Implemented and verified |
| F05 Permissions | root + knowledge; independent reviews | Principal contract, F04 | RLS/cache/tool/history/citation/save/eval revocation; effective read/edit projection | Implemented and verified |
| F06 Library/versions | knowledge + frontend | F05, indexing worker | PDF/DOCX/text, original downloads, immutable replacements, lifecycle/file purge | Implemented and verified; some bulk/filter combinations lack dedicated browser coverage |
| F07 Conversations | knowledge + frontend; identity pagination | F05/F06, generation | Private history/follow-up/refresh, bounded context, full authorized export of 503 messages | Implemented and verified |
| F08 Answer/evidence | knowledge + frontend; identity diagnostics | F06/F07 | Inline/version citations, mobile evidence, feedback, safe failure/rank diagnostics | Implemented and verified |
| F09 Discovery/saved/inbox | knowledge + frontend | F05/F07 | Authorized search/saves, notification deduplication, owner health | Implemented and verified |
| F10 Inventory | identity + frontend; knowledge tools/review | F05 | CRUD/CSV/compare, restricted tools and actual agent/MCP evidence | Implemented and verified |
| F11 Administration | identity + frontend; root accounting | F04/F05 | Scoped keys, revoke/last-used, quotas, audits, ownership and retention | Implemented and verified |
| F12 Evaluation/insights | identity + knowledge + frontend | F05/F07/F11 | Durable eval/cancellation, evidence checks, real metrics/quotas/indexing, diagnostics | Implemented and verified with actual synthetic evaluation; human corpus labels pending |
| F13 UI foundation | frontend | Shared API/routes/types | URLs/themes/responsive/keyboard/scoped queries;16 unit tests + build/type/format | Implemented and verified |
| F14 Delivery | root + independent reviews | All integrated stages | fresh migration 015, existing-data migration, backup/restore, full tests and package smoke | Partial acceptance: native/recovery/image/account checks passed; container inference unverified |

## Executed integration evidence

- Fresh migration 015: 39 tables. Original snapshot restore:2 tenants, 118 documents, 3,590 chunks/embeddings, 20 queries preserved exactly; zero unversioned chunks.
- Final integrated backend suite:90 passed in 40.16 s. Frontend:16 tests, TypeScript, production build and formatting passed.
- Final expanded browser run: 7 passed in 52.3 s, zero failures/skips/flakes. It covers effective grants, safe owner labels/filters, health/quota/indexing and diagnostics alongside the real knowledge/auth journeys. One incorrect new test selector was corrected without production changes.
- Real PDF/DOCX HTTP/worker/version/download: passed in 9.1 s. Actual MCP SDK passed for both preserved workspaces. Actual inventory+document agent: passed in 99.082 s. Actual unavailable-primary/llama.cpp fallback: passed in 5.803 s; zero paid API spend.
- Actual isolated durable-worker evaluation passed in 58.2 s with local retrieval/generation/judge; one synthetic selected question, 599 input / 131 output tokens, zero API spend. Disposable fixtures cleaned; this is not human corpus approval.
- Private post-upgrade backup restored to a new database on migration 015. Counts match the backup-time snapshot, all 15 uploaded original files hash-match, all 5 MFA enrollments decrypt, private overrides mode 0600 and recovery Redis database 14. The running database was not replaced.
- Independent security reviews fixed version binding, access snapshots, inbox privacy, midstream settlement, evaluation/agent diagnostic dependencies, derived-data erasure, history truncation, effective permissions, and recovery queue isolation.
- Final image `3294fb897ce3` built successfully with `Dockerfile.prebuilt`. Runtime UID 10001, exact final assets, health/readiness, personal registration/verification/login, company onboarding and logout all passed. Source hashes for API/conversations/knowledge/retrieval and HTML match the final working tree.
- The uncached container query was attempted under a 448 MiB limit in the shared 2 GiB VM. Native readiness timed out as Docker-hosted dependencies became slow. Root stopped only `atlas-upgrade-verification`; its exit was 137 after the explicit stop, `OOMKilled=false`, and the client received an incomplete response. The native app recovered to ready. Container inference remains unverified; unrelated services and VM allocation were preserved.
- Final Ruff check/format, mypy and whitespace checks passed. Secret scan passed using the reviewed baseline; narrow allowlists cover synthetic test sentinels and document hashes only. No real credential was added to tracked artifacts.

## Final handoff and outstanding acceptance

F01–F13 are implemented with the evidence recorded in [UPGRADE_RELEASE.md](UPGRADE_RELEASE.md). F14 is implemented with native delivery, fresh/upgrade migration, recovery, image build and packaged account/UI checks passing; container inference acceptance is **partial**. All workers completed their assignments. No code was committed, pushed or deployed to production.

Native API, worker and Ollama are running; application URL: http://127.0.0.1:8100. Development inbox: http://127.0.0.1:58025. Temporary fallback and container verification processes were stopped. Existing data and unrelated Docker services remain in place.

Remaining steps requiring a suitable environment or owner input:

1. Re-run the packaged uncached-query check with a separately provisioned Docker VM; do not resize the shared VM or stop unrelated workloads implicitly. Also verify a fully container-hosted generation setup if that is the intended deployment.
2. Review corpus labels as a human before accepting formal retrieval-quality metrics. The successful synthetic evaluation verifies execution only.
3. Run remote CI and any separately authorized production/SMTP/Kubernetes verification. Those checks were not executed locally.

## Completion rule

Required product behavior must be implemented and tested. Resource-unavailable checks remain explicit partial acceptance. Do not label full container-only deployment, formal human corpus-quality approval or remote CI as verified. Keep unrelated local changes/services intact.
