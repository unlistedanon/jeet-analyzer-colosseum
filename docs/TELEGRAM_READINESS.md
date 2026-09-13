# Telegram release-readiness report — 2026-09-12

## Current SOL follow-up — limited verification pass

The SOL payment surface now reuses the existing linked account, Jeet invoice and
finalized verifier. `/access`, `/pay`, `/payment` and `/checkpayment` are DM-only.
The independent payment flag defaults false; a payment stop file disables the
surface without restarting and schedules removal of tracked payment instructions.
Existing entitlement, forensic jobs, account linking and reports remain available.
See TELEGRAM.md's SOL surface section for exact disable and recovery procedures.

The owner explicitly chose SOL and accepted Telegram platform-policy risk. Stars
will not be implemented. No concealment or enforcement-evasion behavior is present.
This decision supersedes the commercial-review recommendation in the historical
first-integration report below.

The A–Q automated private rehearsal uses temporary SQLite records, a fake Telegram
sender, a fake forensic engine and mocked finalized devnet receipts. No live bot,
provider call or funds are involved. A/B help, C browser account linking, D–F invoice
display, G unpaid check, H/I mocked settlement and shared entitlement, J/K honest
unknown-address/reply handling, L explicit mint/wallet async completion, M report
authorization, N rate limit, O general kill switch, P payment disable and Q preserved
access/redacted instructions are asserted. A lone address still reports uncertainty;
it does not silently perform unsupported general wallet tracing.

A full-suite attempt before the user narrowed this pass produced 484 passes and
one timing failure: the legacy lifecycle test treated the first `running` message
edit as completion. Its wait now requires the terminal result. The full suite has
NOT been rerun after this correction, per the user's instruction. Current focused
verification is recorded below; historical full-suite numbers are not current proof.

Focused verification: **94 passed, 2 dependency deprecation warnings, 14.44 seconds**
across `tests/test_telegram.py`, `tests/test_telegram_sol.py` and
`tests/test_sol_payments.py`. This includes the complete mocked A–Q rehearsal.
Log: `build/telegram-sol-focused-pass.log`. No backend/frontend full-suite rerun,
deployment, publication, live Telegram test, provider contact or SOL transfer was
performed after the user narrowed the pass.

Remaining: private bot configuration and an explicitly authorized live rehearsal;
real payment verification remains untested. Production stays OFF. PRIVATE-ALPHA
live readiness remains NO-GO until that controlled setup/rehearsal is completed.
Do not send SOL without separate explicit approval.

Current follow-up files: `.env.example`; `jeet_analyzer_api/sol_payments.py`;
`jeet_analyzer_api/telegram/{adapter,runtime,store,payments}.py`;
`scripts/telegram_admin.py`; `tests/test_telegram.py`; `tests/test_telegram_sol.py`;
`docs/TELEGRAM.md`; `docs/TELEGRAM_READINESS.md`. Earlier work remains intact.

## Historical first-integration report

**Local implementation verified. Production exposure: OFF / not yet recommended.**
The adapter is ready for a controlled test-bot rehearsal. No real Telegram message,
webhook registration, wallet transaction, production server restart, commit or push
was performed during this implementation. The existing frontend was rebuilt locally;
no Telegram frontend source changes were made.

## 1. Exact source files changed for this integration

New files:

- `jeet_analyzer/targeting.py`
- `jeet_analyzer_api/telegram/__init__.py`
- `jeet_analyzer_api/telegram/adapter.py`
- `jeet_analyzer_api/telegram/config.py`
- `jeet_analyzer_api/telegram/formatting.py`
- `jeet_analyzer_api/telegram/gateway.py`
- `jeet_analyzer_api/telegram/runtime.py`
- `jeet_analyzer_api/telegram/store.py`
- `scripts/telegram_admin.py`
- `tests/test_telegram.py`
- `docs/TELEGRAM.md`
- `docs/TELEGRAM_READINESS.md`

Modified files:

- `.env.example`: optional disabled configuration.
- `pyproject.toml`: optional Telegram library extra.
- `jeet_analyzer_api/app.py`: optional lifecycle/router/admin integration and cleanup.
- `jeet_analyzer_api/beta_service.py`: existing seller scan through shared reservations;
  central pause and address validation.
- `tests/test_beta_operator_dashboard.py`: baseline routing test now checks `/`
  serves the SPA and unknown public paths retain the existing 404 behavior.
- `tests/test_sol_payments.py`: baseline dummy RPC assertion parses components,
  avoiding a credential-shaped literal URL in external review packages.
- `scripts/build_external_review.py`: substitutes the exact hosted origin with an
  inert example origin only in two copied package files; strict audit unchanged.
- `tests/test_demo_flagship.py`: checks the packaged example origin.

Other existing dirty files belong to earlier web/payment work and were preserved.
The tracked pre-integration snapshot is `build/telegram-prechange.patch`; it does
not include preexisting untracked files. Generated artifacts under `build/` include
isolated dependency files and test logs; they are not proposed source commits.

## 2. Architecture

Optional Bot API transport -> durable bounded inbox -> thin adapter -> application
gateway -> existing job manager, engine and SQLite budget/payment records.
No forensic logic or membership ledger is duplicated. Domain classification accepts
structured account evidence; unknown is the current lone-address runtime fallback.

## 3. Supported commands

Reply `/jeet`; `/jeet ADDRESS`; raw address in DM; `/scan MINT`;
`/wallet WALLET MINT`; `/jeet MINT WALLET`; `/jeet NUMBER` for choices;
`/status JOB-ID`; `/access`; `/help`; `/start`.
Lone `/wallet WALLET` requests the missing mint. No general fund-flow or claim-intent
investigation is falsely presented as implemented.

## 4. Security model

Numeric sender identity, signed existing web session, explicit same-origin single-use
pairing confirmation, unique account mapping, revocation and owner-bound report URLs.
Authenticated webhook secret, bounded bodies, no passive room scanning or uncertain
send retry. Admin-only audit with pseudonymous user/chat IDs and minimal retained
routing state. Website credentials and payment claims never become Telegram authority.

## 5. Payment/access integration

The bot reads SolPayments membership and its effective configuration. Pending invoices
retain free access only; mocked finalized verification grants paid limits; expired
membership returns to free limits. Payment recipient and commercial terms were not
changed. The real SOL payment smoke test remains uncompleted: no real activation claimed.
The bot offers existing-account linking, not a SOL sale or Stars checkout.

## 6. Limits and availability

Defaults: 5 messages/user/min, 12/chat/min, 30/global/min, 100 queued updates,
900-second automatic delivery window. The web and bot share actual engine admission,
global concurrency, daily credits and member limits. Provider readiness defaults
closed. Stop file pauses bot ingress/work/delivery; already admitted engine work may
finish under its original cap. Single process required. Worker failure closes ingress.

## 7. Verification results

- Baseline before Telegram: **415 passed, 2 failed**.
- Final full backend suite with optional library available: **451 passed**, 2
  dependency deprecation warnings, 24.99 seconds.
- Telegram-specific suite: **34 passed**, 2 dependency warnings, 3.87 seconds.
- Frontend: **44 passed across 10 files**; TypeScript/Vite build passed.
- Python compilation passed; `git diff --check` reported no whitespace errors
  (Windows line-ending notices remain).
- External-review strict allowlist/credential audit passed as part of the full suite.
- Optional library **python-telegram-bot 22.8** installed in isolated
  `build/telegram-deps`; active server dependencies were not replaced.

Logs: `build/telegram-baseline-tests.log`, `build/telegram-final-tests.log`,
`build/telegram-frontend-tests.log`, `build/telegram-frontend-build.log`,
`build/telegram-deps-install.log`.

Tests cover direct/replied input, duplicates, multiple addresses, unsupported input,
numeric identity rejection, group privacy, type-evidence uncertainty, challenge digest,
expiry/reuse/conflicts, unlink, rate/inbox limits, restart recovery, safe formatting,
disabled routes, webhook authentication, browser confirmation, complete async job
lifecycle, cross-owner reports, provider latch, shared scan quota, mocked paid access,
expiry, stale updates, library message serialization and delivery timeout.

## 8. Smoke-test status

An automated local FastAPI smoke flow used a real temporary SQLite store, real job
manager, fake forensic engine and fake Telegram sender: webhook -> account link ->
job -> edited result -> authorized report succeeded. No provider or Telegram calls.
The payment receipt test uses a mocked finalized devnet response; it is not an actual
devnet or mainnet transfer. Library send/edit parameters were exercised offline.

**Live manual bot/chat test: NOT RUN.** Token, secret and bot username were absent
from both process and Windows User environment settings. No unrelated secrets or
accounts were searched. A privately configured test bot/chat is required next.

## 9. Remaining release work

1. Controlled real Telegram rehearsal, including Telegram browser account linking,
   group reply behavior, deletion/blocking and polling/webhook switching.
2. Verify actual Helius capacity before opening the provider admission latch.
3. Review Telegram commercial access rules. Telegram mandates Stars for digital
   sales inside its apps, even where another website exists. This implementation
   does not establish approval for selling SOL memberships through a bot.
   [Official policy](https://core.telegram.org/bots/payments-stars).
4. General lone-address account lookup and fund-flow investigation remain future
   engine work; current unknown response is intentional. Claim analysis unsupported.
5. Full report links currently open authorized JSON, not a new report HTML view.
6. Single-process delivery only; ambiguous initial sends require admin reconciliation.
   No multi-instance lease mechanism or automatic completed-result cache is claimed.
7. Approve production deployment explicitly after rehearsal and commercial review.

## 10. Local commands

See [TELEGRAM.md](TELEGRAM.md) for exact PowerShell commands, private configuration,
polling/webhook setup, stop-file semantics and the controlled rehearsal checklist.
No production configuration was enabled by these tests.

## 11. Recommended commit/PR breakdown

1. Preserve/review the preexisting web and SOL payment changes separately.
2. Baseline regression maintenance: operator routing test and sanitized package copy.
3. Telegram domain contract, transport/storage/gateway, shared job mode, tests and extra.
4. Operator configuration utility and documentation/readiness report.

Use selective hunks for files shared with prior payment work. No commits were made.

## 12. Exposure decision

**Not ready for unrestricted real Telegram users yet.** Offline behavior is verified,
but transport/browser behavior and commercial access have unresolved live release
gates. Ready for a dedicated test bot with provider admission initially closed.
