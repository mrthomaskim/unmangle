"""Who gets filtering: an active Stripe subscription, or an unexpired free trial."""
from datetime import datetime, timezone

# past_due keeps service on while Stripe retries the card (Smart Retries).
PAID_STATUSES = {"active", "trialing", "past_due"}


def now():
    return datetime.now(timezone.utc)


def is_subscribed(user) -> bool:
    return user.get("subscription_status") in PAID_STATUSES


def trial_active(user, at=None) -> bool:
    ends = user.get("trial_ends_at")
    return bool(ends) and (at or now()) < ends


def has_access(user, at=None) -> bool:
    return is_subscribed(user) or trial_active(user, at)


def plan_state(user, at=None) -> str:
    """'subscribed' | 'past_due' | 'trial' | 'expired'"""
    status = user.get("subscription_status")
    if status == "past_due":
        return "past_due"
    if is_subscribed(user):
        return "subscribed"
    if trial_active(user, at):
        return "trial"
    return "expired"


def trial_days_left(user, at=None) -> int:
    ends = user.get("trial_ends_at")
    if not ends:
        return 0
    secs = (ends - (at or now())).total_seconds()
    return max(0, int(-(-secs // 86400)))  # ceil
