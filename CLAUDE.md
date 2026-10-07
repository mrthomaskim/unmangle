# CLAUDE.md

Context for Claude Code working in this repo. Read this first. For infrastructure work, also read `infra/README.md`.

## What this is

**Unmangle** is a Gmail spam filter sold as SaaS. Spammers disguise sender names with junk characters (`FID.ELI.TY LI.FE`, `C.ARSHIELD`, `E^NDURANCE`, `FiDelity      Life^^^^`) so Gmail misses them. Unmangle normalizes From/Subject (strips punctuation, spacing, accents, and emoji; maps 1/0/3/5/4/7 to i/o/e/s/a/t), matches keywords, and moves hits to Spam within seconds of arrival.

It started as the owner's single-user Cloud Run function and was then rebuilt multi-tenant. The owner is a staff-level systems/infra engineer who is fluent in GCP, Terraform/Terragrunt, and Python. Be direct and skip basics.

## Architecture

- **Flask on Cloud Run** (`app/`). Server-rendered Jinja, no JS framework. `wsgi.py` is the entrypoint; it runs under gunicorn in the Dockerfile.
- **Auth:** Google sign-in only (OpenID, PKCE, no passwords). Gmail (`gmail.modify`) is a separate, incremental consent step. The refresh token is Fernet-encrypted (`TOKEN_ENC_KEY` in Secret Manager) and stored in Firestore.
- **New mail:** each user's `users.watch` → one shared Pub/Sub topic `gmail-push` → authenticated push to `/pubsub/gmail` → `history.list` since the stored `historyId` → batch header fetch → classify → `batchModify` to SPAM. If the history is gone (404), it falls back to a 2-day window scan.
- **Background work:** Cloud Tasks queue `backfill` calls `/tasks/backfill`, which runs an inbox cleanup paced at ~33 reads/s/user and chains continuation tasks with a `before:` cursor. Scheduler calls `/tasks/renew-watches` daily (watches expire after 7 days, and expired trials get switched off) and `/tasks/digest` every Monday.
- **Machine endpoints** (`app/hooks.py`) verify Google OIDC tokens and require `email == INVOKER_SA_EMAIL` and `aud == BASE_URL`. In dev/test, a missing token is allowed.
- **Billing:** the 14-day trial lives in-app with no card (`trial_ends_at`). Subscribing goes through Stripe Checkout. The remaining trial days carry over as `subscription_data.trial_end`, but only when 49h or more remain, because Stripe requires at least 48h. Webhooks (`/stripe/webhook`) are the source of truth and are deduplicated in `stripe_events`. Access = subscription status in {active, trialing, past_due} OR trial not expired. When access changes, the Gmail watch starts or stops.
- **Data** (Firestore): `users/{googleSub}`, `users/{uid}/activity/{gmailMsgId}` (TTL on `expires_at`, 90 days), `stripe_events/{id}` (TTL 30 days), `trials/{sha256(email)}` (one trial per Google address, kept even after account deletion).
- **Onboarding:** after Gmail connects, `/onboarding` reads up to 300 Spam-folder headers, groups mangled variants (`suggestions.py`), and offers keyword checkboxes. Then it offers a cleanup.

## Commands

```bash
pip install -r requirements-dev.txt
pytest -q                     # 37 tests; Gmail and Stripe are faked (tests/conftest.py)
cp .env.example .env && python wsgi.py    # local dev, STORE=memory
```

## Infrastructure

Everything is in `infra/` (Terraform with GCS backend, plus `bootstrap.sh`, `release.sh`, and `set-secret.sh`). Terraform owns all infra except the Cloud Run image, which is `ignore_changes`; `release.sh` rolls out images. The OAuth consent screen and OAuth client must be created in the console because there's no API for them. See `infra/README.md`.

## Rules: do not break these

1. **Only From/To/Subject headers are ever read** (`format=metadata`). Never request bodies. The privacy policy and Google's restricted-scope review depend on it.
2. **Never regenerate or rotate `TOKEN_ENC_KEY`** without a re-encryption migration. Every stored Gmail token becomes unreadable otherwise.
3. **Secrets never go in Terraform variables, tfvars, or git.** Use `infra/set-secret.sh`. The only secrets in state are `SECRET_KEY` and `TOKEN_ENC_KEY`, which Terraform generates.
4. **Don't run `terraform destroy`, delete the project, or disable deletion protection** unless the owner explicitly asks.
5. **Before `terraform apply`, show the plan summary** (adds/changes/destroys and anything replaced) and get the owner's OK. Same before creating projects, linking billing, or running `stripe_setup.py` against a live key.
6. **CSP `form-action` must list every redirect target of a form POST** (accounts.google.com, checkout.stripe.com, billing.stripe.com). Chrome enforces it on redirects.
7. **Keep tests green.** Add a test with every behavior change. Fakes live in `tests/conftest.py`.
8. **The repo is public.** Don't commit personal email addresses, project IDs with billing info, `terraform.tfvars`, `backend.hcl`, or `.env`.

## Known constraints and decisions

- `gmail.modify` is a restricted scope. An unverified app is capped at 100 lifetime users, and they see a warning. Past 100 requires OAuth verification plus an annual CASA Tier 2 assessment. The plan is to stay in beta under 100, verify only after there's paying demand, and use a custom domain before applying.
- The OAuth app must be **In production** (unverified is fine). In Testing mode, refresh tokens expire after 7 days.
- Gmail quota is 15k units/user/minute. `messages.get` costs 5. Large scans must stay paced (`gmail.BATCH_INTERVAL_SECONDS`).
- Pricing is $3/mo or $30/yr, with Stripe lookup keys `unmangle_monthly` and `unmangle_annual` (`scripts/stripe_setup.py`).
- Digests go out over SMTP via any provider. If `SMTP_HOST` is unset, they're logged instead of sent.
- Legal pages (`templates/privacy.html`, `terms.html`) are templates with `[BRACKETED]` placeholders and need a lawyer's review before launch.

## Status

- [x] App, tests, Stripe integration, onboarding suggestions, Terraform written (HCL syntax and reference-checked, but not yet planned against a real project)
- [ ] Provision the prod project → see `PROMPT.md`
- [ ] OAuth consent screen and client, Stripe test mode, first release, smoke test
- [ ] Custom domain, legal review, Stripe live mode, Google verification (after beta)
