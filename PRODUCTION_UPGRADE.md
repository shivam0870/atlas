# Atlas production hardening and workbench

This release extends the existing account, MFA, tenant isolation, retrieval and evaluation system. It adds Sources, Compare, Knowledge Map, Playbooks and Briefings, and expands Administration with operations and recovery. No paid model provider is required.

## Product workflows

- **Sources:** administrators connect a public GitHub repository or a tenant-specific folder alias, preview matching files, synchronize manually or on a schedule, and inspect durable run history. Changes become immutable document versions; deleted source files archive the corresponding documents. Disconnecting a source stops synchronization and retains imported company knowledge. GitHub private repositories and OAuth connectors are not supported in this release.
- **Compare:** compare two accessible documents or versions, inspect exact passages and line differences, and follow links to each source version. Deterministic change notes identify review topics without claiming that an AI inference is an established fact.
- **Knowledge Map:** browse authorized inventory, documents, owner teams and explicit relationships as a graph or accessible list. Editors can add relationships; server-side access rules apply to every endpoint and connected resource.
- **Playbooks:** draft and publish source-linked checklists, assign steps, record completion and approval, and inspect run history. Source changes flag a playbook for review. These workflows do not execute infrastructure commands.
- **Briefings:** private daily or weekly subscriptions collect changes to selected knowledge, with exact version links and excerpts. Schedules can be paused, resumed and run manually. Saving a schedule reauthorizes it with the current session; revoked or expired authority pauses scheduled work.

New publication metadata distinguishes draft, published, superseded and archived versions from pending/indexing/ready/failed processing states. Current published, effective evidence is used for ordinary retrieval. Replacement indexing retains the previous searchable version until the replacement is ready. A future effective date can be saved on a draft; publish it when that date arrives.

## Runtime controls

- API startup verifies the actual database role and forced RLS on runtime-accessible tenant tables. The application cannot start using an owner, superuser or bypass-RLS credential.
- Queued ingestion records its submitter and version, rechecks authority before publication and respects cancellation. Retrying a terminal job creates a new attempt with the retrying user's authority.
- Permission revisions prevent cache reuse immediately; a durable worker cleanup removes obsolete cache entries.
- Responses are buffered until every emitted claim passes a conservative literal-passage check against its citations. Unsupported paraphrases are withheld; this is evidence matching, not a factual-truth or calibrated entailment score. Weak evidence abstains. Generation failures offer authorized source excerpts.
- Tenant limits cover storage, upload count/bytes, daily queries, pending jobs, queued generations, concurrent generations and token budgets. Redis admission coordinates generation across processes; ingestion dispatch rotates across tenant work.
- Uploads pass ClamAV before parsing. Missing, stale or failing scanners reject uploads. Rejection records store metadata and hashes; rejected file bytes are discarded. Parsing runs in a disposable bounded process with a cleared environment and platform-specific restrictions.

## Local scanner

Install ClamAV and prepare Atlas's private loopback scanner:

```sh
brew install clamav
python3 scripts/scanner.py update
python3 scripts/scanner.py start
python3 scripts/scanner.py status
```

The scanner listens on `127.0.0.1:53310`. `UPLOAD_SCAN_REQUIRED` defaults to true. The operator scheduler refreshes native signatures periodically; Docker ClamAV includes its own updater. Do not disable scanning to work around an outage. Restore the scanner and retry the upload.

For local Docker, enable the scanner with `docker-compose --profile security up -d clamav`. Production Compose requires its scanner health check before starting the API. Provision enough memory for the scanner and model workloads; the shared historical 2 GiB local VM is not a capacity acceptance environment.

Folder aliases are configured by the host operator in `ATLAS_SOURCE_FOLDERS`, a JSON object mapping `<tenant UUID>:<alias>` to an absolute directory. Only that tenant can choose the alias. File access rejects symlinks and traversal. Keep this mapping in the process environment, outside user-editable knowledge documents.

## Operations, backups and recovery

Administration → Operations shows readiness, tenant queue depth, failed jobs, deduplicated alerts, backup/restore evidence and release records. Company administrators can retry/cancel their jobs and acknowledge their alerts. They cannot download host backups or invoke host recovery.

`scripts/local.py start` also starts the native operator scheduler. It creates daily backups and weekly restore drills, using a single-process lock. It retains backups and recovery databases for operator inspection. Operators must manage disk retention; the scheduler never deletes an existing database.

For a local application update, `python3 scripts/local.py restart --services api worker operator-scheduler` restarts only those managed services and keeps the model process running. Apply database migrations after draining the API and worker, before starting the updated application.

```sh
.venv/bin/python scripts/backup.py
.venv/bin/python scripts/recovery_drill.py .local/backups/atlas-<timestamp>
.venv/bin/python scripts/operations_schedule.py --once
```

Backups use an exported PostgreSQL snapshot, copy the original files referenced by that snapshot, preserve the matching private configuration, and record integrity hashes. A concurrently removed original causes the backup to fail rather than produce a falsely complete bundle. Backup directories are private and contain account credentials and encryption keys.

Restore drills create a new recovery database, verify document and permission table hashes, validate original files and enrolled MFA secrets, migrate the recovered schema and check that the runtime role cannot read documents without tenant context. They do not switch the running application or consume its queue. The restored database is retained for inspection. Recovery helpers currently target this project's loopback Docker PostgreSQL deployment; hosted deployments need an operator adapter for their database and storage.

## Release gate and rollback

```sh
.venv/bin/python scripts/release.py fingerprint
.venv/bin/python scripts/release.py verify --name <release> --image <repository>@sha256:<digest>
.venv/bin/python scripts/release.py activate <manifest.json> --env-file <private-deployment.env> --dry-run
.venv/bin/python scripts/release.py rollback <previous-manifest.json> --env-file <private-deployment.env> --dry-run
```

Build the image with label `io.atlas.source-sha256` equal to the fingerprint. The gate records source, prompt and model hashes plus schema revision and refuses acceptance without passing checks and an owner-reviewed corpus baseline. Deployment requires an accepted manifest, the matching pinned image, and the same schema revision. Remove `--dry-run` only when deliberately deploying. Schema changes use verified recovery, never automatic down-migrations.

Human corpus-label review remains necessary. Passing engineering tests does not create a reviewed production-quality baseline. Container capacity and production deployment require separate acceptance on the intended host.

Current workflow limits: document selectors show the 100 most recently updated accessible documents; Knowledge Map and source imports are bounded. Conflict detection highlights matching statements with different quantities and does not claim to find every semantic contradiction. The parser's separate process, resource limits and platform restrictions provide defense in depth; they are not a complete operating-system sandbox.
