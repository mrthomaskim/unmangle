"""One-time Stripe setup. Safe to re-run: finds existing objects by lookup key / metadata.

    STRIPE_SECRET_KEY=sk_test_... python scripts/stripe_setup.py --base-url https://your.app

Creates: product, $3/month + $30/year prices (lookup keys unmangle_monthly / unmangle_annual),
a Customer Portal configuration, and (with --webhook) the webhook endpoint.
Run it once with your test key and once with your live key.
"""
import argparse
import os
import sys

import stripe

MONTHLY, ANNUAL = "unmangle_monthly", "unmangle_annual"
EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
    "invoice.payment_failed",
]


def find_or_create_product(name):
    for p in stripe.Product.search(query="metadata['app']:'unmangle'").auto_paging_iter():
        return p
    return stripe.Product.create(name=name, metadata={"app": "unmangle"},
                                 description="Moves disguised spam out of your Gmail inbox.")


def ensure_price(product, lookup, amount, interval):
    found = stripe.Price.list(lookup_keys=[lookup], active=True, limit=1).data
    if found:
        return found[0]
    return stripe.Price.create(product=product.id, currency="usd", unit_amount=amount,
                               recurring={"interval": interval}, lookup_key=lookup,
                               transfer_lookup_key=True, tax_behavior="exclusive")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="Public URL of the app, e.g. https://unmangle.app")
    ap.add_argument("--name", default="Unmangle")
    ap.add_argument("--webhook", action="store_true", help="Also create the webhook endpoint")
    args = ap.parse_args()

    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or sys.exit("Set STRIPE_SECRET_KEY")
    base = args.base_url.rstrip("/")
    mode = "LIVE" if stripe.api_key.startswith("sk_live") else "TEST"
    print(f"Stripe {mode} mode")

    product = find_or_create_product(args.name)
    monthly = ensure_price(product, MONTHLY, 300, "month")
    annual = ensure_price(product, ANNUAL, 3000, "year")
    print(f"Product {product.id}\n  monthly {monthly.id}\n  annual  {annual.id}")

    portal = stripe.billing_portal.Configuration.create(
        business_profile={"headline": f"Manage your {args.name} subscription",
                          "privacy_policy_url": f"{base}/privacy",
                          "terms_of_service_url": f"{base}/terms"},
        default_return_url=f"{base}/dashboard",
        features={
            "customer_update": {"enabled": True, "allowed_updates": ["email", "address"]},
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "subscription_cancel": {"enabled": True, "mode": "at_period_end",
                                    "cancellation_reason": {"enabled": True, "options": [
                                        "too_expensive", "unused", "missing_features", "other"]}},
            "subscription_update": {"enabled": True, "default_allowed_updates": ["price"],
                                    "proration_behavior": "create_prorations",
                                    "products": [{"product": product.id,
                                                  "prices": [monthly.id, annual.id]}]},
        },
        metadata={"app": "unmangle"},
    )
    print(f"Portal configuration {portal.id}  ->  set STRIPE_PORTAL_CONFIG={portal.id}")

    if args.webhook:
        hook = stripe.WebhookEndpoint.create(url=f"{base}/stripe/webhook", enabled_events=EVENTS,
                                             description=f"{args.name} app")
        print(f"Webhook {hook.id}\n  STRIPE_WEBHOOK_SECRET={hook.secret}   (shown once; store it now)")


if __name__ == "__main__":
    main()
