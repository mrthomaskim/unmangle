# Prompt: provision the Unmangle production infrastructure

Paste the block below into Claude Code from the repo root. Run Claude Code somewhere that can reach `*.googleapis.com` and the Terraform registry: your Mac with `gcloud` and `terraform` installed, or Google Cloud Shell with Claude Code installed. Some sandboxed cloud sessions block Google Cloud APIs.

---

```text
Read CLAUDE.md and infra/README.md first. Then provision a brand-new GCP project for
Unmangle production using the Terraform in infra/terraform, following infra/README.md.

Context
- I'm a staff infra engineer; be concise. I use a personal Google account (no GCP organization).
- Region us-central1, Firestore nam5. Budget alert $10/month on my billing account.
- Stripe stays in TEST mode for now. No custom domain yet (use the run.app URL).
- Project ID: propose one like unmangle-prod-<4 random digits> and confirm with me.

How to work
1. Preflight: check `gcloud auth list`, `gcloud config list`, `terraform version` (>= 1.6),
   and that you can reach googleapis.com. If anything is missing or blocked, stop and tell me
   exactly what to install or run. Run `pytest -q` to confirm the app is green.
2. Run `gcloud billing accounts list`, show me the accounts, and ask which one to use.
3. Before running infra/bootstrap.sh (creates the project + links billing), show me the exact
   command and wait for my OK.
4. terraform init with backend.hcl, then `terraform plan -out=tfplan`. Summarize the plan
   (counts of add/change/destroy and the key resources), check it against CLAUDE.md, and wait
   for my OK before `terraform apply tfplan`. If the first apply fails on API propagation,
   wait 60s and re-plan/apply; explain any other error before retrying.
5. Walk me through the console-only OAuth steps (infra/README.md section 3) one at a time:
   tell me exactly what to click and the values to paste (use `terraform output`), then wait
   while I do it. I'll run ./infra/set-secret.sh myself for secret values. Never ask me to
   paste a secret into this chat, and never echo secrets.
6. Stripe test mode: tell me the stripe_setup.py command (section 4); after I run it and set
   the secrets, add stripe_portal_config to terraform.tfvars, plan, show me, apply.
7. Run ./infra/release.sh and fix any build or deploy errors.
8. Guide me through the smoke test (section 6) and check Cloud Run logs for errors.
9. Finish with: a checklist of what was created, every output value, anything left for me
   (e.g. publish OAuth app, legal pages, custom domain), and the monthly cost estimate.

Rules
- Follow the "Rules: do not break these" section of CLAUDE.md. Never run terraform destroy,
  delete resources outside the plan, or rotate TOKEN_ENC_KEY.
- Don't commit terraform.tfvars, backend.hcl, .env, or any secret. The repo is public.
- If you change any Terraform or app code to fix a problem, keep `pytest -q` green, keep
  `terraform fmt` clean, and tell me what you changed and why. Then update the Status
  section of CLAUDE.md.
```
