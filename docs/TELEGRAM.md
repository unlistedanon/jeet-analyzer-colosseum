# Interactive Telegram adapter

Status: local implementation, disabled by default, no live bot rehearsal yet.
Production deployment/tunnel controls are intentionally excluded from this public
candidate; see the omitted-script boundary in BETA_OPERATIONS.md.
This is an explicitly requested command interface, separate from outreach tools.

## Architecture

Telegram webhook or optional polling -> bounded SQLite inbox -> normalized command
adapter -> JeetGateway -> existing BetaJobManager -> existing EngineAdapter.
The existing beta database stores identities, reservations, jobs and SOL memberships.
Additional `telegram_*` tables hold only transport links, short-lived choices,
delivery watches and audit events. There is no second forensic or payment engine.
`python-telegram-bot` is imported only when enabled; transport tests inject a sender.
The optional extra supports 22.x; local serialization was tested with 22.8.
See [library documentation](https://docs.python-telegram-bot.org/en/stable/telegram.bot.html).

## Commands and honest limitations

| Input | Behavior |
| --- | --- |
| Reply `/jeet` to a message | Extract validated addresses from reply text/caption |
| `/jeet ADDRESS`, raw address in DM | Safe unknown classification; request explicit scan/context |
| Multiple addresses in a reply | Per-user/per-chat numbered choices, five-minute expiry; `/jeet NUMBER` |
| `/scan MINT` | Existing token seller scan, same reservation ledger |
| `/wallet WALLET MINT` | Existing bounded mint/wallet cluster investigation |
| `/jeet MINT WALLET` | Same investigation with explicit mint-first ordering |
| `/wallet WALLET` | Ask for mint context; no invented generic wallet tracing |
| `/status JOB-ID` | Owner-authorized stored result; group must match original requesting chat |
| `/access`, `/pay`, `/payment` | DM-only linking, shared entitlement, and SOL invoice when independently enabled |
| `/checkpayment [SESSION]` / Check Payment button | Existing owner-bound finalized verifier; no new invoice |
| `/help`, `/start` | Usage instructions |

Command context is not on-chain classification evidence. `targeting.classify_target`
can classify a supplied, timestamped jsonParsed account receipt. The adapter currently
has no budgeted general-account lookup, so an isolated address remains unknown.
`ClaimObservedFlow` is a reusable unsupported-result contract, not an intent model.
Token seller scans and mint/wallet investigations work through explicit commands.
The bot never labels a person a scammer or asserts common ownership.

## Identity, payment and report security

Only numeric `message.from.id` is identity. Usernames, forwarded authors, bot messages,
channel posts, anonymous sender-chat posts and edited messages cannot establish it.
Messages older than 24 hours or over five minutes in the future are discarded.
Group traffic must contain an explicit supported command; no passive chat scanning.

DM `/access` creates a five-minute random challenge, stored as a SHA-256 digest.
Open it in the browser holding the existing Jeet session. The page displays the
requesting numeric Telegram ID. An explicit same-origin POST confirms the link.
One Telegram identity per Jeet account; links cannot silently replace each other.
Unlink via authenticated same-origin DELETE `/api/beta/telegram/link`; this also
revokes outstanding delivery watches. For Telegram's embedded browser, sign in or
restore the same account there, or use the existing authenticated external browser.

Membership is read directly from SolPayments. Text, screenshots, Telegram payment
claims and invoice creation do not grant paid entitlement. Existing free access
retains its configured limits. Only the existing finalized verification path changes
membership. Expiry returns admission to the free configuration. No receiving wallet,
price, term, wallet signing, transaction submission or payment verification rule is
changed by this adapter. The Telegram surface creates/reuses ordinary Jeet SOL
invoices on the same linked account. There are no Telegram-native payment invoices.

The product decision is SOL-only, with the Telegram platform risk explicitly accepted
by the owner. Telegram requires Stars for digital goods/services sold inside Telegram,
including when another website exists. No Stars integration, concealment, payment
misrepresentation, alternate enforcement identities or evasion is implemented.
If warned, disable the SOL surface as described below and investigate requirements
separately. [Official policy](https://core.telegram.org/bots/payments-stars).

Report buttons target the existing owner-protected JSON receipt endpoint. They
contain no bearer/share token and require the website cookie. This first version
does not add a dedicated HTML report page. Group summaries intentionally omit raw
transactions, free-text provider errors and wallet relationships. Group users can
see the requesting user's bounded result statuses; request privately for privacy.

Webhook ingress checks `X-Telegram-Bot-Api-Secret-Token` with constant-time comparison.
The normal body-size boundary applies. Disabled adapter has no routes or bot imports.
Authenticated admin audit: GET `/api/beta/telegram/admin`; summary also joins beta
admin metrics. User/chat audit IDs are keyed pseudonyms. Normalized routing metadata
is wiped after processing; no message-body or claim history is retained. Active
delivery requires raw chat/message IDs. Finished forensic delivery and non-payment
audit records expire after seven days on subsequent admissions; identity links
persist until unlinked. Payment audit events persist for reconciliation. Tracked
payment-message IDs persist until successfully redacted.
Protect database/backups with the same access controls as the beta ledger.

## Budgets, delivery and failure behavior

Default accepted-message limits: 5/user/minute, 12/chat/minute, 30/global/minute;
100 queued updates. BEGIN IMMEDIATE makes admission/dedup atomic. Full inbox returns
503 for webhook retry. Rate-limited messages are acknowledged without a bot reply;
one global rejection audit per minute avoids an attacker-sized rejection log.
Admitted update IDs remain seven days; stale-message rejection prevents old replay.
Existing active-job dedup applies; completed results are not automatically reused.

Both web and bot reserve against existing global concurrency, daily global credits,
per-owner scan limits and paid daily estimated-credit pools. Every engine invocation
keeps configured RPC/page/transaction/time bounds. `JEET_TELEGRAM_PROVIDER_READY=1`
is an operator admission latch, not proof that Helius currently works. Leave it zero
until provider readiness/budget is verified. Failures produce generic notices, no
provider URLs. Actual engine receipts remain authoritative about incomplete coverage.

Queued requests receive one response, then the bot edits it as status changes.
Unknown delivery after a crash is never automatically resent. A 900-second delivery
watch timeout stops automatic edits; `/status` can retrieve the eventual receipt.
An uncertain initial send can leave a job with no watch; admin audit can reconcile it.
Blocked/deleted destinations stop their watch after a failed edit. There are no
outreach messages or automatic retries of uncertain sends.

Create the configured stop file to pause ingestion, processing and automatic edits
without restart. Already executing engine jobs retain their original budgets and
may finish; this is not a hard cancellation switch. Set `JEET_TELEGRAM_ENABLED=0`
and restart to remove routes entirely. An unexpected worker exit makes ingress 503.
Run exactly one application worker/instance per database/bot: startup recovery is
not designed for multi-process competition. Do not enable uvicorn reload.

## Local verification (no credentials or provider calls)

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install --target build/telegram-deps "python-telegram-bot>=22.7,<23"
$env:PYTHONPATH=(Resolve-Path build/telegram-deps).Path
.\.venv\Scripts\python.exe -m pytest tests/test_telegram.py -q
.\.venv\Scripts\python.exe -m pytest -q
Push-Location frontend
npm.cmd test
npm.cmd run build
Pop-Location
```

Dependency isolation leaves the active server environment untouched. Tests create
temporary databases and fake engine/provider receipts; they do not settle real SOL.

## Controlled bot rehearsal (not performed)

Use a dedicated test bot and a consenting test chat, with private environment values.
Install `.[ui,test,telegram]` in a separate test virtual environment, configure beta
auth and a separate beta database/output directory per BETA_OPERATIONS.md, and set
the variables in `.env.example`. The public origin must match the beta public origin.
Start with provider readiness zero; this exercises linking/help/rejections without
spending provider credits. Enable provider admission only for an explicitly bounded
rehearsal after checking capacity. Do not copy the production membership database.

```powershell
# After private test configuration, use polling without exposing an HTTP webhook:
$env:JEET_TELEGRAM_ENABLED='1'
$env:JEET_TELEGRAM_MODE='polling'
$env:JEET_TELEGRAM_PROVIDER_READY='0'
.\.venv-telegram\Scripts\python.exe -m uvicorn jeet_analyzer_api.app:create_app --factory --host 127.0.0.1 --port 8133 --workers 1 --no-access-log
```

Use the configured authenticated HTTPS test origin for link confirmation. Polling
refuses an existing webhook; it never deletes it automatically. Manual configuration
commands below contact Telegram and must target the authorized test bot only:

```powershell
.\.venv-telegram\Scripts\python.exe -m scripts.telegram_admin status
.\.venv-telegram\Scripts\python.exe -m scripts.telegram_admin delete-webhook
# After setting webhook mode and a reachable test HTTPS origin/secret:
.\.venv-telegram\Scripts\python.exe -m scripts.telegram_admin set-webhook
```

These commands retain pending updates. Never enable request/debug/access logs that
record tokens, pairing URLs, or message bodies. Verify reply-to-message, private link,
unknown target, multiple choices, bounded job/edit, unauthorized status, unlink,
duplicate delivery, provider pause and stop-file behavior. Keep production OFF until
the rehearsal and release review are approved.

## SOL surface: one invoice system and one entitlement ledger

`TelegramSolSurface` is presentation/orchestration only. In a DM it resolves the
numeric Telegram sender through the existing account link, then calls the existing
`SolPayments.pending_invoice`, `create_invoice`, `invoice_for_owner`, `verify`, and
`membership` methods. Only `verify` updates `sol_invoices` / `sol_memberships`.
No account or wallet is generated by Telegram. No second payment ledger exists.
The new `telegram_payment_messages` table contains chat/message IDs for redaction,
not balances, payment state or entitlement authority.

`/access`, `/pay`, `/payment` show ACTIVE without another purchase when membership
exists. Otherwise, when enabled, they reuse the same unexpired web invoice or create
one under the core's existing limits. Amount, network, recipient and instruction-bound
reference come directly from that invoice. Mainnet users must use the website's
existing Solana Pay wallet link/QR; a normal SOL send lacking the reference is not
eligible. A reference is not a memo. Devnet sessions explicitly say test-only and
never advertise a mainnet transfer URI. No new signing/submission code exists.

The Check Payment callback contains only the 32-character invoice ID. Its sender
must own that invoice through their current link. `/checkpayment` without an ID
checks the current owned pending invoice. No signature argument is accepted by this
surface; the existing website's signature entry remains available. Callback clicks
and pasted payment assertions cannot grant access. Unknown, malformed or another
account's session is rejected without exposing its contents. Global transport limits,
core five-invoice/day limit and ten-second per-invoice check throttle still apply.

Payment verification calls run off the async event loop. The independent maintenance
task can redact instructions while an RPC check is in flight. A link change during
that check prevents sending its result to a newly linked identity. A transaction that
was already being verified may still settle on its original account; disabling a
surface never cancels or erases a legitimate payment or entitlement.

### State projection

| Authoritative observation | Telegram result |
| --- | --- |
| Core creates/reuses invoice | PAYMENT REQUIRED / awaiting_payment |
| No qualifying finalized reference transaction | No qualifying finalized payment detected; do not pay again |
| Unconfirmed transaction | Still awaiting; no entitlement |
| Core validates finalized receipt and grants | ACTIVE / settled |
| Already settled same invoice | Idempotent receipt; no extra membership extension |
| Same signature claimed for another invoice | replay_rejected; no grant |
| Invoice expired | Expired; no replacement purchase silently created |
| Provider outage/network mismatch | Unavailable; do not pay again |
| Invalid transfer | Rejected or no qualifying finalized payment; no grant |

The current verifier queries finalized data only. It does not expose an observed or
confirming stage, so Telegram does not invent one. A timely payment can be verified
after invoice expiry if its transaction timestamp is within the original invoice
window, exactly as the existing core allows. Late transfers fail. A receipt from an
expired membership does not reactivate it when checked again.

### Independent flag and exact emergency disable

`JEET_TELEGRAM_SOL_PAYMENTS_ENABLED=false` by default; `true` or `1` enables only this
surface. Unknown values fail closed. Environment values are read at request time,
but editing another shell's environment does not update an already running process.
For an immediate no-restart override use `JEET_TELEGRAM_SOL_PAYMENTS_STOP_FILE`:
default `build/telegram-sol-payments.STOP` relative to the app's working directory.

For the documented repository-root launch, disable immediately:

```powershell
New-Item -ItemType File -Force -Path '.\build\telegram-sol-payments.STOP'
```

If the test/server uses a custom stop-file path, create that exact file instead.
No code change, redeploy or restart is needed. New wallet/reference instructions,
invoice creation and verification calls from Telegram stop. Account linking,
existing entitlement, `/jeet`, `/status` and authorized report access remain available.
The general Telegram kill switch is independent. The web payment system is untouched.

Maintenance attempts to replace tracked payment messages with a neutral disabled
notice and removes their buttons, normally on the next one-second tick. Failed edits
remain visible in admin `payment_messages_pending_redaction` and retry no faster than
once/minute. That counter tracks all visible payment messages even while enabled.
Do not claim remote removal until the edits are confirmed. Deleted/inaccessible
messages, unknown initial sends, screenshots, forwarded copies or offline clients
cannot be guaranteed recalled. Reconcile those cases manually with Telegram.

For persistent shutdown, also set `JEET_TELEGRAM_SOL_PAYMENTS_ENABLED=false` in the
private launch configuration. Keep the stop file present. Re-enabling requires a
deliberate operator decision: enable the flag and remove only the exact stop file.
There is no automatic re-enable, replacement bot or migration mechanism.

### Platform warning response

1. Create the payment stop file immediately and set the persistent flag false.
2. In a private test chat, verify `/access`, `/pay`, `/payment`, `/checkpayment`
   expose no wallet/reference or payment button; inspect redaction outcomes in admin.
3. Confirm an existing entitled account still runs an explicit bounded investigation
   and can retrieve its own report; verify account linking still works.
4. Preserve `sol_invoices`, `sol_memberships`, `sol_payment_audit`, Telegram payment
   audit events and database backups. Do not revoke purchased access.
5. Investigate Telegram requirements separately. Do not conceal or bypass enforcement.

### Audit and recovery

Existing admin-only Telegram audit events include account ID, pseudonymous Telegram
user/chat ID, invoice ID, required lamports, receiving address, resulting state,
settled signature/timestamp, entitlement flag, replay/idempotency outcomes, sanitized
errors and feature-flag state. The core's original invoice and audit tables retain the
authoritative receipt. The bot never claims it observed an unconfirmed signature.
No RPC URLs, seeds, signing material or raw provider exceptions enter these events.

After an outage, restart the same single-process service on the same database.
Use `/checkpayment` to reconcile the existing session; do not request another payment
solely because a check failed. Uncertain transport sends require manual reconciliation.
Expired unpaid sessions are not silently replaced by the bot; check first, then use
the existing website/operator workflow for a deliberately new request if needed.

### Private rehearsal, no real funds

Run the full mocked A–Q sequence using temporary databases:

```powershell
$env:PYTHONPATH=(Resolve-Path build/telegram-deps).Path
.\.venv\Scripts\python.exe -m pytest tests/test_telegram_sol.py -k private_alpha_smoke -v
```

It exercises start/help, authenticated account linking, invoice display, prepayment
check, mocked finalized devnet verification, shared entitlement, unknown target/reply,
explicit async investigation, authorized report, rate limit, general kill switch and
payment-only disable. This is a simulation, not a real blockchain transfer or Bot API
delivery. Production remains OFF. Do not send real funds without separate explicit
approval for a tiny payment test.

For a real private bot, privately configure a dedicated token, username and HTTPS
test origin with a separate database as above. Add these test-only values to the
launching shell, leaving the general Telegram enable flag off until setup is complete:

```powershell
$env:JEET_SOL_NETWORK='devnet'
$env:JEET_SOL_PAYMENTS_ENABLED='1'
$env:JEET_TELEGRAM_SOL_PAYMENTS_ENABLED='true'
$env:JEET_TELEGRAM_SOL_PAYMENTS_STOP_FILE='build/private-alpha/payments.STOP'
# Configure JEET_SOL_RECIPIENT as a public devnet test receiving address privately.
# Match paid credits to the separate test beta's global cap; no real funds.
```

Use the offline A–Q test for settlement until a separate real test is authorized.
No fake settlement endpoint or production entitlement backdoor was added.
