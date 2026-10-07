# Infrastructure runbook

Everything except the GCP project, the Terraform state bucket, and a few console-only steps is managed by Terraform in `infra/terraform`. Run the commands from the repo root in **Cloud Shell** (shell.cloud.google.com), which already has `gcloud`, `terraform`, and `python3`. A Mac works too, with `gcloud auth login` and `gcloud auth application-default login` done first.

```
bootstrap.sh ──▶ project + billing + gs://<project>-tfstate      (gcloud, once)
terraform    ──▶ APIs, SAs/IAM, secrets, Firestore+TTL, Pub/Sub, Tasks, Scheduler,
                 Artifact Registry, Cloud Run (placeholder image), budget
console      ──▶ OAuth consent screen + OAuth Web client        (no Terraform/API support)
set-secret   ──▶ OAuth client, Stripe, SMTP values → Secret Manager (never in state)
release.sh   ──▶ Cloud Build image → Cloud Run rollout            (every code change)
```

Terraform ignores the Cloud Run image (`lifecycle.ignore_changes`), so `terraform apply` never rolls back a release. Everything else drifts back to code on apply.

## 1. Bootstrap the project

```bash
gcloud billing accounts list                     # copy the ACCOUNT_ID
PROJECT_ID=unmangle-prod-$RANDOM BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX ./infra/bootstrap.sh
```

This creates the project (with no organization on a personal account), links billing, creates the versioned state bucket, and writes `infra/terraform/backend.hcl` and `terraform.tfvars`. Both files are gitignored. Edit `support_email` and `mail_from` in `terraform.tfvars`.

## 2. Terraform

```bash
cd infra/terraform
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan        # about 65 resources on the first run
terraform apply tfplan
terraform output                  # note oauth_redirect_uri and stripe_webhook_url
cd ../..
```

The first apply takes about 5 minutes. If it fails with "API not enabled" or "has not been used in project", newly enabled APIs are still propagating. Run `terraform apply` again.

The generated `SECRET_KEY` and `TOKEN_ENC_KEY` live in the state bucket and in Secret Manager. `TOKEN_ENC_KEY` has `prevent_destroy`. Never replace it: every stored Gmail token is encrypted with it.

## 3. OAuth consent screen and client (console only)

In the console for the new project, open **Google Auth Platform**:

1. **Branding:** app name, user support email, and developer contact. Add the app domain, homepage, `/privacy`, and `/terms` URLs once you have a custom domain.
2. **Audience:** choose External, then **Publish app** (status *In production*). In Testing mode, refresh tokens expire after 7 days and every user's filter silently stops. Unverified production apps are capped at 100 users.
3. **Data access → Add scopes:** `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`, `.../auth/gmail.modify`.
4. **Clients → Create client → Web application:**
   - Authorized redirect URI: the `oauth_redirect_uri` output.
   - For local development, create a *separate* client with `http://localhost:8080/auth/callback`.
5. Store the values:
   ```bash
   ./infra/set-secret.sh GOOGLE_CLIENT_ID
   ./infra/set-secret.sh GOOGLE_CLIENT_SECRET
   ```

## 4. Stripe

Start in test mode.

```bash
pip install --user stripe
STRIPE_SECRET_KEY=sk_test_... python3 scripts/stripe_setup.py \
  --base-url "$(terraform -chdir=infra/terraform output -raw base_url)" --webhook
./infra/set-secret.sh STRIPE_SECRET_KEY
./infra/set-secret.sh STRIPE_WEBHOOK_SECRET      # the whsec_... printed above (shown once)
```

Put the printed `bpc_...` in `terraform.tfvars` as `stripe_portal_config`, then run `terraform apply` again.

## 5. Release the app

```bash
./infra/release.sh
```

This builds with the `unmangle-build` service account, deploys the new image, and health-checks `/healthz`. Run it after every code change and after every `set-secret.sh`. Running instances only read secrets at startup.

## 6. Smoke test

1. Open the `base_url` output. Sign in with a second Google account and connect Gmail.
2. The onboarding page should list Spam-folder suggestions. Run a cleanup.
3. Send that account an email with "C.ARSHIELD" in the subject. Within seconds it should land in Spam and appear under Activity.
4. Subscribe with card `4242 4242 4242 4242`, open Manage billing, and cancel immediately in Stripe test mode. The dashboard should show filtering paused.
5. Check the logs: `gcloud run services logs read unmangle --region us-central1 --limit 50`.

## 7. Custom domain (needed for Google verification)

Verify the domain in Search Console. Then map it, either with Cloud Run domain mappings or a global HTTPS load balancer with a serverless NEG. Then:

- Set `custom_domain_url = "https://yourdomain"` in `terraform.tfvars` and run `terraform apply`. This updates `BASE_URL`, the push endpoint, OIDC audiences, and the Scheduler URLs.
- Add the new redirect URI to the OAuth client, and update the Stripe webhook URL in the Stripe dashboard.
- Run `./infra/release.sh`.

## Going live with Stripe

Rerun `stripe_setup.py` with `sk_live_...` (live mode has separate products, prices, and webhooks). `set-secret.sh` both Stripe secrets, update `stripe_portal_config`, run `terraform apply`, then `./infra/release.sh`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `SERVICE_DISABLED` / "API has not been used" on first apply | APIs are still propagating. Wait a minute and re-run `terraform apply`. |
| Budget creation 403 | Your account needs Billing Account Administrator on that billing account. Or set `billing_account = ""`. |
| `allUsers` IAM binding fails | An org policy (Domain Restricted Sharing) blocks public services. Use a personal project or an org exception. |
| Cloud Build `PERMISSION_DENIED` right after the first apply | IAM grants take up to 2 minutes to propagate. Re-run `release.sh`. |
| Pub/Sub push returns 401/403 | The app's `INVOKER_SA_EMAIL` or `BASE_URL` doesn't match the subscription's OIDC audience. Run `terraform apply` so they match. |
| Pub/Sub push never arrives | The user's watch isn't set up. Check `PUBSUB_TOPIC` and the `gmail-api-push@system.gserviceaccount.com` publisher binding. |
| `invalid_grant` in logs | The user revoked access, or the OAuth app is still in Testing mode. Publish it (step 3.2). |

## Tearing down

`deletion_protection = true` guards Cloud Run and Firestore. To remove everything, set it to `false`, apply, run `terraform destroy`, then delete the project. Delete the project only if you mean it: Firestore data can't be recovered.
