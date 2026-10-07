# Unmangle

A multi-user SaaS version of your Gmail spam filter. People sign in with Google, connect Gmail, pick keywords (with suggestions drawn from their own Spam folder), and Unmangle moves disguised spam (`FID.ELI.TY LI.FE`, `C.ARSHIELD`, `E^NDURANCE`) out of their inbox seconds after it arrives. Every account gets a 14-day trial with no card. After that it's $3/month or $30/year through Stripe.

```
             ┌──────────── Cloud Run: Flask app (gunicorn) ────────────┐
Browser ───▶ │ /login /connect-gmail     Google sign-in + Gmail consent│
             │ /dashboard /rules /activity /settings /onboarding       │
Stripe ────▶ │ /stripe/webhook           subscription state            │──▶ Firestore
Gmail ─▶ Pub/Sub push ─▶ /pubsub/gmail  history.list → classify → Spam │   (users, activity,
Cloud Tasks ─▶ /tasks/backfill          inbox cleanup, chunked         │    trials, events)
Scheduler ──▶ /tasks/renew-watches (daily)  /tasks/digest (Mon 8am)     │
             └─────────────────────────────────────────────────────────┘
```

| Piece | Choice | Why |
|---|---|---|
| Auth | Google sign-in only, PKCE, no passwords | Nothing to reset or leak. Gmail is a separate, incremental consent step. |
| Gmail tokens | Fernet-encrypted in Firestore, key in Secret Manager | A database leak alone exposes no tokens. |
| New mail | One shared Pub/Sub topic. Each push carries `emailAddress` + `historyId` | Reacts in seconds, costs nothing when idle, and `history.list` reads only what changed. |
| Data read | `From`, `To`, `Subject` headers only (`format=metadata`) | Least access, easy to defend in Google's review. |
| Billing | In-app trial, then Stripe Checkout + Customer Portal | No Stripe objects for trial users. Remaining trial days carry into Stripe on subscribe. |
| Retention | Activity rows expire after 90 days (Firestore TTL) | Required by the privacy policy; keeps storage tiny. |

## Read this before launch: Google's restricted-scope rules

`gmail.modify` is a **restricted** scope.

- **Up to 100 users, unverified:** works today. Users see "Google hasn't verified this app". The cap counts every account that has ever granted access, and it's a lifetime cap.
- **More than 100 users:** you need OAuth app verification plus an annual **CASA Tier 2** security assessment from a Google-approved lab, at a cost of a few thousand dollars per year. Google also requires:
  - a verified domain you own, with the homepage, privacy policy, and terms on that domain
  - a privacy policy that includes the Limited Use disclosure (`templates/privacy.html` already has it)
  - a demo video showing the consent screen and how each scope is used
  - a written justification for `gmail.modify`: "move matching messages to Spam and back; headers only"

Suggested order: launch as a beta under 100 users, confirm people pay, then apply for verification. No code changes are needed between those stages.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # fill it in (see below)
pytest                          # 37 tests, no network needed
python wsgi.py                  # http://localhost:8080
```

**Google OAuth client.** In Google Auth Platform, go to Clients → Create client → **Web application**. Add the authorized redirect URI `http://localhost:8080/auth/callback`. Put the ID and secret in `.env`. While the app is in Testing mode, add yourself under Audience → Test users.

**Stripe (test mode).**
```bash
STRIPE_SECRET_KEY=sk_test_... python scripts/stripe_setup.py --base-url http://localhost:8080
stripe listen --forward-to localhost:8080/stripe/webhook    # copy the whsec_... into .env
```
Pay with card `4242 4242 4242 4242`. To test a trial running out without waiting 14 days, set `TRIAL_DAYS=0` for new sign-ins, or use a Stripe test clock on the customer.

**Gmail push** needs a public URL, so leave `PUBSUB_TOPIC` empty locally. Use **Run cleanup** on the dashboard to exercise filtering against your real inbox. Locally it runs inline instead of going through Cloud Tasks. `STORE=memory` keeps everything in RAM, and restarting the server signs you out.

## Deploy to GCP

Prerequisites: a GCP project with billing, `gcloud` logged in, and Python 3 locally.

1. **OAuth consent screen** (Google Auth Platform):
   - Branding: app name, support email, app domain, homepage, `/privacy`, `/terms`.
   - Audience: External. Click **Publish app** for production, or stay in Testing and add test users. Testing-mode tokens expire every 7 days.
   - Data access: add `openid`, `userinfo.email`, `userinfo.profile`, and `gmail.modify`.
   - Clients: create a **Web application** client. You'll add the production redirect URI after step 2.
2. **Deploy:**
   ```bash
   PROJECT_ID=your-project \
   GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... STRIPE_SECRET_KEY=sk_test_... \
   SUPPORT_EMAIL=you@yourdomain.com ./deploy.sh
   ```
   This enables the APIs and creates Firestore with TTL policies, two service accounts, the secrets (`SECRET_KEY` and `TOKEN_ENC_KEY` are generated for you), the Pub/Sub topic and authenticated push subscription, the Cloud Tasks queue, Cloud Run, and both Scheduler jobs. It prints the URL when it finishes.
3. **Add the redirect URI** it printed (`https://…/auth/callback`) to your OAuth client.
4. **Stripe:**
   ```bash
   STRIPE_SECRET_KEY=sk_test_... python3 scripts/stripe_setup.py --base-url https://YOUR_URL --webhook
   STRIPE_WEBHOOK_SECRET=whsec_... STRIPE_PORTAL_CONFIG=bpc_... PROJECT_ID=your-project ./deploy.sh
   ```
   Repeat with `sk_live_...` when you go live. Live mode has its own products, webhook, and portal config.
5. **Custom domain (recommended; Google verification requires one):** map the domain to Cloud Run, using a Cloud Run domain mapping or a load balancer. Then re-run with `BASE_URL=https://yourdomain.com ./deploy.sh` and update the OAuth redirect URI and the Stripe webhook URL to match.
6. **Weekly digest email:** set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, and `MAIL_FROM` for any SMTP provider (Postmark, SendGrid, or SES), then re-run. Set up SPF and DKIM for the sending domain. If SMTP isn't configured, digests are only logged.

Re-run `./deploy.sh` after code changes. It's idempotent, and it never regenerates `TOKEN_ENC_KEY`. Losing that key makes every stored Gmail token unreadable, and every user would have to reconnect.

## How the money flow works

| User state | Filtering | What they see |
|---|---|---|
| Trial (days 1–14, no card) | On | "Free trial, N days left" and plan buttons |
| Subscribes during trial | On | Checkout with `trial_end` set to their remaining trial, so the first charge lands on day 15 (only when ≥49h remain, a Stripe minimum) |
| `active` / `trialing` | On | Plan, renewal date, Manage billing (portal) |
| `past_due` | On (grace while Stripe retries) | Banner: update your card |
| Trial over or `canceled` / `unpaid` | Off. The Gmail watch is stopped by webhook or the daily job | Banner: choose a plan |

Webhooks are the source of truth and are deduplicated by event ID. The success page also syncs immediately so the dashboard is correct on the first load. One trial per Google address: deleting an account and signing up again doesn't reset the trial, and neither does connecting a second Gmail account.

**Turn on in the Stripe dashboard:** Smart Retries, emails for failed payments and upcoming renewals, and Stripe Tax if you need to collect sales tax (prices are created as tax-exclusive).

## Operations

- **Logs:** `gcloud run services logs read unmangle --region us-central1`. Logs are structured JSON with `uid`, `reason`, and `status` fields.
- **Alert on revoked grants:** create a log-based alert on `jsonPayload.message="gmail grant revoked"`. Those users see a "Reconnect Gmail" banner.
- **Gmail quota:** each user gets 15,000 units per minute. Push handling uses about 10–30 units per email. Cleanup is paced at roughly 33 reads per second per user and continues across chained Cloud Tasks with a time cursor, so large inboxes never time out.
- **Cost at 100 users:** roughly $0–5/month (Cloud Run scales to zero, plus Firestore, Pub/Sub, and Tasks free tiers). Set a budget alert. Stripe fees on a $3 charge are about $0.39.

## Project layout

```
app/
  auth.py         Google sign-in, Gmail connect, trial assignment
  billing.py      Checkout, portal, webhook → subscription state → watch on/off
  gmail.py        token handling, batch header reads, history processing, cleanup, watch
  filtering.py    normalize() + classify(): keywords, built-ins, safe-sender list
  suggestions.py  groups Spam-folder senders into keyword suggestions
  hooks.py        Pub/Sub push, Cloud Tasks, Scheduler endpoints (OIDC-verified)
  views.py        pages and form handlers
  store.py        Firestore + in-memory implementations
  templates/, static/
scripts/stripe_setup.py   products, prices, portal config, webhook
deploy.sh                 full GCP stack
tests/                    fakes for Gmail + Stripe; covers the flows above
```

## Before you take money

- [ ] Have a lawyer review `templates/privacy.html` and `templates/terms.html`, and fill in the `[BRACKETED]` fields.
- [ ] Set up a support inbox for `SUPPORT_EMAIL`.
- [ ] Run Stripe in live mode: rerun `stripe_setup.py` with your live key, then redeploy with the live secrets.
- [ ] Add a budget alert in GCP Billing.
- [ ] Test the full flow end to end on the production URL with a second Google account: sign up, suggestions, cleanup, subscribe, cancel in the portal, filtering stops.
