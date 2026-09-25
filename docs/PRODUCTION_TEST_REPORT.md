# Production upgrade verification — 2026-09-24

The new production controls and five workbench tabs were verified against an isolated PostgreSQL database at migration `023`, Redis database `15`, a dedicated API on port `8112`, and separate frontend assets and uploaded files. The browser used real local PostgreSQL, Redis, Mailpit, ClamAV, embedding models, and Ollama. Browser acceptance did not mock API responses, retrieval, scanning, or generation.

## Results

| Check | Result |
| --- | --- |
| Complete backend suite | **141 passed** in 117.81 seconds |
| Additional real inference-outage regression | **1 passed** in 41.22 seconds |
| Frontend component tests | **29 passed** |
| Production frontend build | Passed; final browser bundle `index-MlURePfr.js` |
| Complete Chrome browser suite | **11 passed, 1 failed** in 2.4 minutes |
| Authentication suite rerun, unchanged | **3 passed** in 22.3 seconds |
| Tester-owned Python lint and repository whitespace checks | Passed |

The single full-browser failure was an HTTP connection reset (`socket hang up`) on the existing MFA test's replay-login request. The API remained healthy, and the entire authentication suite, including that exact case, passed on an unchanged rerun. All twelve browser journeys therefore have passing evidence; the first complete browser invocation itself was not fully green. No test assertion was removed or authentication implementation changed to obtain the rerun result.

## Exercised behavior

- **Permissions:** tenant isolation, document grants, cited passages, conversations, saved answers, histories, background-job authority, membership revocation during indexing, and streaming access changes. Browser checks verified restricted upload, company-switch cancellation, and revoked evidence becoming unavailable.
- **Versions:** actual replacement indexing, exact passages from versions 1 and 2, publication changes, version links in briefings, and source changes disabling stale playbook execution.
- **Document and answer safety:** real clean-file scanning and real rejection of the inert EICAR antivirus test signature without document creation; invalid file type rejection; misleading/unsupported citation regressions; a real Ollama answer passing passage verification.
- **Resource and failure handling:** concurrent storage quota enforcement, job quota enforcement despite resource-level RLS, bounded tenant scheduling, cancellation, retry attribution, and real retrieval with an unavailable inference endpoint. The outage test used a refused local TCP connection, retained authorized source excerpts, and excluded the other tenant's source text.
- **Operations:** real scanner readiness, tenant-scoped job listings, management access, and platform-operator separation. Recovery-drill evidence is recorded separately by the coordinating agent.
- **Sources:** real public-GitHub unavailable-repository preview/sync error handling in the browser; database integration tests for approved tenant-bound folders, symlink confinement, import, repeated synchronization, and removed-file archival.
- **Compare:** browser selection of two real indexed versions and exact before/after passages; evidence-backed change summaries and review-required flags in backend tests.
- **Knowledge Map:** browser relationship creation, inspection, and persistence between real documents.
- **Playbooks:** browser creation/publication, version-pinned source selection, checklist execution, completion, approval, and stale-source blocking.
- **Briefings:** browser schedule creation, manual execution, exact current-version excerpts, pause persistence, and cross-tenant privacy; database tests for quota consumption, topic filtering, and fair due-work scheduling.
- **Existing journeys:** account verification, invitations, password reset and session revocation, one-use MFA recovery, inventory/CSV operations, service credential revocation, administration, accessibility, privacy choices, themes, mobile navigation, and immutable document restoration.

## Reproduce

Prerequisites are the standard `.local/upgrade-test.env` isolated database configuration, migration head, installed dependencies, local Mailpit, current ClamAV signatures, embedding models, Ollama, and Chrome. Run these commands from the repository root:

```sh
.venv/bin/python scripts/isolated.py .venv/bin/pytest -q
.venv/bin/python scripts/isolated.py .venv/bin/python scripts/workbench_e2e.py run
```

The browser harness validates isolated database/Redis settings, builds under `.local/workbench-e2e/dist`, starts its own API and worker, and stops those processes after the test run. It refuses an occupied port rather than stopping an existing service. It uses local SMTP and separate test uploads, and does not rebuild or replace the live console assets. Optional spec filenames can follow `run` for a focused check.

Private execution artifacts are under `.local/workbench-e2e/` and `artifacts/private/`. A sanitized first-full-browser summary is saved as `artifacts/private/production-browser-full-attempt.json`; the latest Playwright result contains the unchanged authentication rerun.

## Boundaries

This is functional, security-regression, and recovery-readiness evidence, not a sustained load/soak test or a completed human-reviewed answer-quality evaluation. The public GitHub browser scenario deliberately used an unavailable repository; successful synchronization was checked with approved local folders. Signature refresh availability, external repository availability, and production capacity still require operating checks in the deployment environment.
