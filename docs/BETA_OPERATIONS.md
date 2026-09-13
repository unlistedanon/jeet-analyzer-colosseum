# Public beta operations

> Public candidate boundary: production deployment and tunnel controls are intentionally
> excluded. The private scripts `restart_beta_public.ps1`, `smoke_beta_journey.ps1`,
> `start_beta_laptop.ps1`, `start_beta_named_tunnel.ps1`, `start_beta_quick_tunnel.ps1`
> and `stop_beta_laptop.ps1` are not shipped. Hosting discussion below documents the
> application contract; it is not an instruction to run those absent controls.

Jeet beta is a single-process FastAPI service that serves the built React client, runs the existing read-only forensic CLI through its in-process adapter, and persists quotas, jobs, result metadata, and feedback in SQLite. It is deliberately designed for one container with one persistent volume. Do not run multiple replicas against this SQLite database or process-local worker queue.

## Security and truth boundary

- The browser submits only a token mint and public wallet.
- Provider URLs and credentials remain server-side.
- Invite and admin codes are stored as SHA-256 hashes; active invite sessions are invalidated when their code ID is removed.
- Sessions are HMAC-signed, short-lived, `HttpOnly`, and `SameSite=Strict`. Set `JEET_BETA_COOKIE_SECURE=1` behind HTTPS.
- A configured hosted beta rejects requests explicitly forwarded as plain HTTP and emits HSTS on requests forwarded as HTTPS. Direct loopback health checks without forwarding headers remain available.
- New beta requests use fixed server-owned forensic budgets. Client payloads cannot raise them.
- `JEET_BETA_ENABLED=0` rejects new investigations while authenticated users can still read previously stored results. When hosted beta credentials are configured, legacy unauthenticated local investigation routes remain unavailable even while this kill switch is off.
- `COMMON_CONTROL=NOT_PROVEN` remains part of the forensic result contract. Relationships are evidence, not ownership.

## Generate private credentials

From repository root on Windows CMD:

```cmd
.venv\Scripts\python scripts\generate_beta_codes.py
```

The default generates 20 high-entropy invites, one independent admin credential, their hashes, and a session secret under ignored `build\beta-admin\` files. Raw codes are never printed. Copy the three server environment assignments from the generated private `beta-secrets-*.env` file into the server's private `.env`; distribute individual raw invites from `invite-codes-*.txt` out of band. Never commit either file.

## Required environment variable names

Live beta startup requires:

- `JEET_BETA_ENABLED`
- `JEET_BETA_CODE_HASHES`
- `JEET_BETA_ADMIN_CODE_HASH`
- `JEET_BETA_SESSION_SECRET`
- `JEET_BETA_COOKIE_SECURE`
- `SOLANA_RPC_URL`
- `HELIUS_API_KEY` when Helius history enrichment is used

Persistent paths:

- `JEET_BETA_DATA_DIR`
- `JEET_BETA_DB_PATH`
- `JEET_BETA_OUTPUT_DIR`
- `JEET_BETA_FRONTEND_DIST`

Beta admission limits:

- `JEET_BETA_MAX_CONCURRENT` (default `1`)
- `JEET_BETA_MAX_RUNS_PER_CODE_PER_DAY` (default `1`)
- `JEET_BETA_MAX_ESTIMATED_CREDITS_PER_RUN` (default `60000`; server-owned and operator-configurable)
- `JEET_BETA_GLOBAL_DAILY_CREDIT_CAP` (default `120000`)
- `JEET_BETA_SESSION_TTL_SECONDS` (default `28800`)

Server-owned forensic limits:

- `JEET_BETA_DAYS` (default `30`)
- `JEET_BETA_GRAPH_DEPTH` (default `3`, the supported maximum)
- `JEET_BETA_MAX_WALLETS` (default `25`)
- `JEET_BETA_MAX_PAGES` (default `200` per provider acquisition)
- `JEET_BETA_REQUEST_TIMEOUT` (default `45`)
- `JEET_BETA_PROVIDER_RETRIES` (default `1`)
- `JEET_BETA_PROVIDER_BACKOFF_CAP` (default `8`)
- `JEET_BETA_MAX_RPC_REQUESTS` (default `1500`, including retries)
- `JEET_BETA_MAX_SIGNATURES` (default `40000`)
- `JEET_BETA_MAX_TRANSACTIONS` (default `20000`)
- `JEET_BETA_FUNDING_LOOKBACK_DAYS` (default `365`)
- `JEET_BETA_MATERIALITY_INVENTORY_PCT` (default `0.01`)
- `JEET_BETA_DEEP_FORENSIC` (default `1`; traverses eligible cross-asset/high-signal relationships within every server-owned ceiling)

Request counts are factual. Credit counters are method-weighted estimates because the provider does not expose authoritative per-request billing through this application. The shared provider context enforces the configured per-run ceiling before every actual RPC/DAS attempt, including retries. Cache hits and local evidence deduplication do not consume estimated credits.

The per-run value is reserved only while a job is queued/running. A successfully completed job is charged its recorded estimate, so early evidence exhaustion does not consume the full ceiling. Failed runs without trustworthy completion telemetry are charged their full reservation conservatively. Global daily admission sums active reservations plus terminal actual estimates; this prevents an active run from crossing the daily cap without treating unused per-run headroom as spent.

The beta expands breadth-first and evidence-first: seed inventory/history, direct evidence, depth-ordered relationship traversal, then fresh related inventory. Deep forensic traversal is enabled by default for beta investigations, but it remains bounded by the configured page, wallet, request, signature, transaction, and estimated-credit ceilings. Pagination ends when the provider has no continuation token. Graph expansion ends when no evidence-backed wallet candidate remains. A seeded cluster audit does not spend on a later mint-wide market pass because that supplemental context does not feed its already-completed lifecycle, relationship, or inventory conclusions; the dedicated seller `scan` workflow owns market-wide context. Any required page, wallet, depth, signature, transaction, request, or credit ceiling remains explicit incomplete/unresolved coverage.

The browser remembers the latest investigation ID for the active beta session, resumes polling after refresh, and retries transient status-read failures with bounded backoff. Terminal results remain durable in SQLite. If the engine atomically wrote a valid cluster receipt before a late adapter/filesystem error, the worker preserves and returns that receipt instead of replacing it with an empty failure; its original coverage fields are never upgraded.

## Local beta without Docker

Install and build once:

```cmd
cd /d C:\path\to\jeet-analyzer
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[ui,test]"
cd frontend
npm ci
npm run build
cd ..
```

Create a private `.env` from `.env.example`, add generated hashed credentials and provider credentials, and load it into the backend process environment. The application does not parse `.env` files itself. Then start:

```cmd
set JEET_BETA_ENABLED=1
set JEET_BETA_COOKIE_SECURE=0
.venv\Scripts\python -m jeet_analyzer_api
```

Open `http://127.0.0.1:8000/`. The protected admin view is `http://127.0.0.1:8000/?admin=1`. Starting the service and loading the gate do not call a provider; provider work begins only after an admitted investigation.

## Single-container startup

Docker was not available in the development checkpoint, so the package was validated statically rather than executed. On a host with Docker:

```cmd
docker compose -f docker-compose.beta.yml build
docker compose -f docker-compose.beta.yml up -d
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

The compose file binds to loopback, mounts a durable named volume at `/data`, drops Linux capabilities, makes the root filesystem read-only, and leaves TLS/public routing to the host's HTTPS reverse proxy. Before internet exposure:

1. Put `127.0.0.1:8000` behind an HTTPS reverse proxy.
2. Set `JEET_BETA_COOKIE_SECURE=1`.
3. Restrict inbound traffic to HTTPS.
4. Verify `/ready`, the invite gate, malformed-input rejection, protected admin access, and client-bundle secret absence.
5. Keep exactly one application replica unless SQLite/process-local jobs are replaced with shared durable infrastructure.

## Stop, backup, and restore

Gracefully stop new work first with the kill switch, then stop the container:

```cmd
docker compose -f docker-compose.beta.yml stop jeet-beta
docker compose -f docker-compose.beta.yml cp jeet-beta:/data/jeet-beta.sqlite3 build\beta-admin\jeet-beta-backup.sqlite3
```

Keep the backup outside source control. Restore only while the service is stopped. The investigation output directory is supporting evidence and should be backed up with the database when retention matters.

## Rotate or revoke access

- To revoke one invite, remove its `code-id:hash` entry from `JEET_BETA_CODE_HASHES` and restart the container. Existing sessions for that ID fail immediately after restart.
- To rotate all invites, generate a new set, replace the hash list, and restart.
- To rotate the admin credential or session secret, replace its hash/secret and restart. Rotating the session secret invalidates all sessions.
- Never put raw codes in frontend source, images, issue trackers, logs, or Git.

## Emergency kill switch

Set `JEET_BETA_ENABLED=0` in the private runtime environment and restart the service. The restart truthfully marks any interrupted queued/running jobs as `SERVER_RESTARTED`; no result is strengthened. Admin metrics and previously stored authenticated reports remain available. The global daily cap rejects new work when terminal estimated use plus active reservations cannot safely admit another full investigation ceiling.

## Operational checks

- `GET /health`: process and read-only capability liveness.
- `GET /ready`: persistent storage plus enabled-beta credential readiness.
- `GET /api/beta/admin/metrics`: separately authenticated run, estimate, budget, duration, and feedback metrics.
- Logs must be handled as private operational data even though exception transport redacts credential-bearing URLs.
- The Week 1 offline routes remain `/?demo=flagship` and `/?demo=week1-video`.

No live paid investigation is required to validate deployment mechanics. Perform a real live beta smoke test only as a separate, explicitly authorized provider-spend gate.


## Public access (default)

`JEET_BETA_PUBLIC_ACCESS=1` removes the invite requirement. `GET /api/beta/session`
automatically creates a signed, HttpOnly visitor cookie; no provider work occurs.
Admin credentials and a strong session secret are still required. Invite hashes
are optional in public mode. Existing valid invite sessions retain their identity.
Set `JEET_BETA_PUBLIC_ACCESS=0` to restore the legacy invite-only flow.

Each visitor has isolated reports, feedback and membership. The visitor cookie
lasts one year; clearing cookies or switching browsers loses that identity.
Per-visitor daily quotas are a convenience limit, not a unique-person guarantee:
a new browser identity can obtain a fresh quota. The unchanged shared daily
credit cap, per-run ceiling, concurrency limit and kill switch bound provider use.
Admin and legacy local routes remain protected. Public session responses must
never be cached. Opening access does not make existing reports public.
## First-use improvements

The main page offers a saved, anonymized example from the existing public demo.
Opening it performs no investigation and consumes no provider credits. Reports
start with plain-language answers for observed sales, the observed seed balance,
and direct transfers of the target token. Missing evidence stays unestablished.

The protected admin page shows daily browser identities reaching each step.
Browser events are `visit`, `example_opened`, `scan_attempt`, and `result_viewed`;
result-view events require an owned report with a saved result. Admissions and
feedback are recorded by the server. Completed visitors are derived from durable
investigations submitted on the current UTC day. The steps are not a sequential
conversion rate; refreshed browsers are deduplicated and cookie resets can count
as new visitors. Counts can include operator tests and browser blocking can
undercount visits. No historical visit counts are fabricated.

Usage counters are stored next to the primary database in `*.usage.sqlite3`.
Only UTC day, daily HMAC visitor digest and allowlisted event name are stored.
No IPs, wallet addresses, mint inputs, raw session IDs or free-text payloads are
stored in the counters. Rows older than 30 days are pruned on the next event.
Counter write failures do not block scans. Preserve this optional database when
backing up usage history. Example and simulated end-to-end QA used no live scans.

## SOL checkout and daily membership allowance

The private deployment used a 0.5 SOL / 30-day mainnet plan. This candidate includes
no laptop launcher or receiving-wallet default; configure an authorized recipient as
`<SOL_RECEIVING_PUBLIC_ADDRESS>`. This is a one-time purchase,
not automatic recurring billing. `JEET_SOL_MEMBER_RUNS_DAILY=0` means no daily
scan-count limit. `JEET_SOL_MEMBER_DAILY_CREDITS=250000` is the daily shared pool
across that member's scans, resetting at midnight UTC. Credits do not roll over.
The per-scan ceiling is reduced to the remaining member and service-wide daily
allowance under the same SQLite admission transaction. Active scans reserve
credits; completed scans release unused credits. Failed scans without trustworthy
telemetry conservatively consume the reservation. Earlier same-day free scans
also count against the member's daily total after upgrade.

The service-wide provider cap is unchanged. A membership is an allowance, not
reserved provider capacity; shared service limits and availability still apply.
Existing memberships retain their recorded terms (`daily_credits=0` for legacy
per-scan plans). New invoices snapshot the current plan; subsequent price changes
do not alter issued invoices.

Customers generate and save an app recovery code before the UI shows a payment
request. Only its SHA-256 hash is stored. It restores the same browser identity,
including membership and reports; it is not a wallet key. Replacement revokes the
old code. Recovery failures are throttled. Never request a customer's code in
support messages.

The QR and wallet link encode a Solana Pay transfer request with an invoice
reference. CHECK FOR PAYMENT searches at most five finalized reference
transactions and validates the exact recipient, amount, reference placement,
network, transaction result, and invoice time window. Manual signature entry is
available when reference lookup is insufficient. This app never signs or submits
transactions. To pause new payments, set `JEET_SOL_PAYMENTS_ENABLED=0` in the
launching process environment before restarting; existing payments can still be
verified and purchased terms remain available.
