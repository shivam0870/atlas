# Atlas public demo

**App:** https://cavalry-habitable-sureness.ngrok-free.dev

**Project showcase:** https://shivam0870.github.io/atlas/

**Hosting spend:** ₹0; uses the existing Mac, internet connection and electricity.

This is the real React application and FastAPI backend. It requires the host Mac to be awake, online and running the services. It is not an always-on cloud deployment. The free ngrok welcome screen may appear; choose **Visit Site** to continue.

## Using the app

1. Create your own account and use the verification email to activate it.
2. Sign in and create a company or personal workspace.
3. Open Library, upload PDF/DOCX/Markdown/text and wait until indexing finishes.
4. Ask a question about the document, open the citation and ask a follow-up.

There is no shared public administrator password. Each visitor starts with their own workspace; the verification fixtures are private test companies.

## Runtime on the configured Mac

The dedicated instance lives in the Git-ignored `.local/public-demo/` directory. It has a separate PostgreSQL database, Redis database 12 on each Atlas Redis service, separate original-file storage and a separate MFA encryption key. The existing daily-use database/uploads are preserved. Local model files and the native Ollama process are shared; simultaneous demos compete for that model's capacity.

Only `127.0.0.1:8120` is forwarded through ngrok. The database, Redis, model server, developer inbox and ngrok inspection API stay on loopback. HTTP payload inspection is disabled. API access logs are disabled to avoid logging verification-token URLs. The runtime configuration excludes administrative database credentials; migrations use a separate private file. SMTP uses certificate-verified STARTTLS with a trusted CA bundle.

Run from the repository root:

```sh
.venv/bin/python scripts/public_demo.py status
.venv/bin/python scripts/public_demo.py start
.venv/bin/python scripts/public_demo.py stop
```

The launcher requires the provisioned private instance, configured ngrok, the existing Docker database/Redis services and native Ollama. `start` sets the actual public `APP_URL` before starting the API and worker, and starts `caffeinate` to prevent idle sleep. `stop` signals only the processes whose recorded identity still matches this dedicated instance. It preserves the ordinary local app and all data. After a reboot, start the local dependencies first with `python3 scripts/local.py start`, then start the public demo.

This launcher manages the configured reference Mac; it does not provision new cloud hosts or reconstruct private credentials from a Git clone. Never commit `.env`, SMTP settings, ngrok credentials, backups or test-account passwords.

## Verification on the public HTTPS origin

- Desktop 1440px and mobile 390px login pages, real UI/assets and no horizontal overflow.
- Real login with Secure/HttpOnly/SameSite=Strict cookies; logout invalidation; unverified login denied; cross-origin mutations denied.
- PDF and DOCX upload, actual worker/embedding indexing, exact page/version references and byte-for-byte original downloads.
- A separate browser upload through the actual library form.
- Two actual local-model generations streamed through ngrok: question → answer → versioned citation → save → follow-up → refresh. Mobile evidence drawer checked.
- Company switching denied the other company's document download.
- Replacement indexed a new PDF version while old originals, source text and historical conversation remained intact.
- Gmail's certificate chain and SMTP authentication passed; a real public signup request was accepted and its verification email was accepted for sending.

**Email verification remains pending owner confirmation.** The full browser knowledge journey used a separately provisioned test account; its email step was not counted as passed. No inbox was read and the actual unverified signup account was left unchanged. Public password-reset and invitation email clicks have not been exercised. This is an honest limitation of the current public verification, separate from the previously passed local account tests.

The SMTP TLS change also passed seven isolated account regression tests, Ruff and Python type checks. Public evidence contains no secrets; private screenshots and fixture credentials stay under `.local/public-demo/`.

## Backup and restart

```sh
.venv/bin/python scripts/public_demo.py backup
.venv/bin/python scripts/public_demo.py start
```

`backup` intentionally stops the dedicated public processes before dumping the database and copying original files. It leaves the demo stopped until `start`. Bundles are under `.local/public-demo/.local/backups/atlas-<UTC timestamp>/`, with database and file hashes, runtime configuration (including the matching MFA key), migration configuration, worker configuration and instance metadata. These are sensitive private bundles. Store an additional private copy off this Mac.

Recovery must target a new database first: verify the bundle hashes, restore `atlas.dump` using `pg_restore --exit-on-error`, retain the matching MFA key and upload files, and apply migrations using the private migration configuration. Stop demo writers before switching every database URL in its `.env`, `worker.env` and `migration.env` to the recovered database; update `instance.json` as well. Use isolated Redis queues/caches, then verify login, files and old citations before reopening. Preserve the original database and backup until recovery passes. Never use down-migrations or delete Docker volumes as a recovery shortcut.

The original project's recovery rehearsal is recorded in `UPGRADE_RELEASE.md`. A new public-demo restore rehearsal has not been performed; do not describe this public endpoint as production-ready or highly available.
