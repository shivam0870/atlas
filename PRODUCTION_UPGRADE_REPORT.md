# Atlas upgrade verification — 24 September 2026 UTC

Five specialist roles contributed: frontend, backend, AI/runtime, end-to-end testing, and independent review. The implementation extends the existing security and retrieval system and adds Sources, Compare, Knowledge Map, Playbooks, and Briefings. Administration now includes operations and recovery. See [the operating guide](PRODUCTION_UPGRADE.md) for behavior, configuration, and supported boundaries.

## Verification

| Check | Evidence |
| --- | --- |
| Backend/security regression suite | 141 passed against the isolated upgrade database at migration `023` |
| Generation outage | Additional integration case passed with real indexing/retrieval and a refused local inference connection; authorized excerpts survived and another tenant's evidence was excluded |
| Frontend components | 29 passed |
| Browser journeys | 11 of 12 passed on the full invocation; all 3 authentication journeys passed unchanged on rerun after one transient connection reset. Every journey has passing evidence. |
| Static checks | Python Ruff, whole-source mypy, TypeScript and frontend Prettier passed |
| Upload scanning | Real ClamAV rejected the standard inert EICAR antivirus test file through the browser upload journey |
| Public GitHub happy path | The real connector downloaded `psf/requests` → `README.md` (2,906 bytes); no document was imported into the live workspace |
| Backup and recovery | Restored a new database; 12 document/access table hashes matched, 19 originals verified, 7 MFA enrollments decrypted, unscoped application reads blocked |

Recovery evidence is private under `.local/recovery-drills/e9431711-6940-42c0-8d0a-5ffc8465d48d.json`. The associated backup is `.local/backups/atlas-20260924T182054065483`; it contains private configuration and must not be committed or shared. The recovered database was migrated through `023` and retained for inspection.

The reviewer corrected misleading citations, stale history metadata, access rechecks, historical playbook evidence deletion, schedule fairness, download bounds and tenant-wide job counting under document-level RLS. Regression cases cover these failures.

The [detailed test report](docs/PRODUCTION_TEST_REPORT.md) records the tested journeys and the transient browser failure without treating the first browser invocation as a clean pass.

## Local installation

The native database was migrated from `015` to `023` after verifying there were no pending ingestion jobs and draining the local API and worker. The console was built with the tested `index-MlURePfr.js` bundle. The API, worker and operator scheduler restarted successfully; `http://127.0.0.1:8100/ready` returned ready. Both new Sources and Operations endpoints returned HTTP 401 without authentication. The shared model process and separate public demo were left running.

A second backup and restore drill ran successfully after the upgrade, recording evidence for the Operations page. Backup: `.local/backups/atlas-20260924T182738803868`. Drill: `.local/recovery-drills/85935553-4db8-4286-8af9-b2b9ae9aebd9.json`. It again verified all 12 table hashes, 19 originals, 7 MFA enrollments and application-role isolation. The scheduler now manages daily backups, weekly drills and signature refreshes.

## Release boundary

The corpus baseline remains `pending_human_review`. The release gate refuses production acceptance until the human-reviewed quality baseline and engineering checks pass. Native execution and recovery evidence do not establish production container capacity or hosted-storage acceptance. The existing public demo was subsequently upgraded on 25 September 2026; dedicated cloud production deployment remains outside this rollout.

Citation verification deliberately requires literal source support and can decline useful paraphrases. Quantity-based conflict flags are review aids. GitHub sources currently support public repositories; folder aliases must be configured by the host operator. Parser restrictions provide defense in depth, not a complete operating-system sandbox. Additional boundaries are documented in the operating guide.

## Public rollout — 25 September 2026

The upgrade was pushed to `shivam0870/atlas` as `74c4425` and deployed to [the existing public demo](https://cavalry-habitable-sureness.ngrok-free.dev). The [GitHub Pages showcase](https://shivam0870.github.io/atlas/) also deployed successfully. Twelve additional deployment guard tests passed for database-target validation, credential separation and recovery configuration.

The dedicated instance was backed up, migrated through `023`, backed up again and restarted. Its new operator scheduler successfully restored the public snapshot into a separate database: 12 table hashes and 10 original uploads matched, and unscoped application reads were denied. The public instance had no enrolled MFA secrets to decrypt; the earlier native drill verified all 7 native enrollments. Private evidence paths and operation instructions are in [PUBLIC_DEMO.md](PUBLIC_DEMO.md).

[Hosted engineering CI passed](https://github.com/shivam0870/atlas/actions/runs/36101046984), including scanner-backed backend checks, frontend checks and browser account journeys. [The showcase deployment passed](https://github.com/shivam0870/atlas/actions/runs/36101047042). The separate corpus workflow reported the existing pending human-review state; a green workflow does not mean that corpus-quality acceptance occurred.

The final public HTTPS smoke run passed all five grouped checks with zero browser errors. It verified secure login, all new tabs, Operations/scanner health, actual upload and indexing, actual local generation and exact citations, cross-tenant denials, workspace switching and mobile navigation. Two earlier harness issues (an ambiguous selector and a five-second login assertion) were corrected; the final complete invocation passed. No product authorization assertion was weakened, and no email journey was counted in this public smoke.
