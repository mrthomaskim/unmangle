from datetime import timedelta

import pytest

from app.access import has_access, now, plan_state, trial_days_left
from app.filtering import classify, clean_allow_entry, clean_keyword, normalize
from app.suggestions import build_suggestions


@pytest.mark.parametrize("raw,expected", [
    ("FID.ELI.TY LI.FE", "fidelitylife"),
    ("FiDelity      Life^^^^^^^", "fidelitylife"),
    ("F1del1ty_Life", "fidelitylife"),
    ("Fídelity-Life 🎉", "fidelitylife"),
    ("C.ARSHIELD", "carshield"),
    ("E^NDURANCE", "endurance"),
    ("A.UT.O.PLAN.A.DV.ISO.RS", "autoplanadvisors"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_classify_builtins_and_structure():
    assert classify('"C.ARSHIELD" <x@spam.com>', "Your quote", "me@gmail.com").startswith("Built-in")
    assert classify("A <a@b.com>", "hi", "mrthomaskim@gmail.com@7c1x6symq8kns8edt.__random_anm") \
        == "Built-in: forged recipient"
    assert classify("X <D_eploying.Ltd.BIMMER.Ltd.MCK.Ltd.MCK@corsetdeals.com>", "hi", "me") \
        == "Built-in: junk sender address"
    assert classify("Y <SuRvEy-offe.Ltd.BIMMER.Ltd.ICK.Ltd.ICK.Ltd.SuRvEy@kitayamatriangle.com>", "hi", "me") \
        == "Built-in: junk sender address"
    assert classify("Acme <billing@acme.ltd.uk>", "Invoice", "me") is None
    assert classify("Mom <mom@family.com>", "Dinner Sunday?", "me@gmail.com") is None


def test_classify_user_keyword_defaults_off_and_allowlist():
    frm = '"Metro via SafeOpt" <promo@safeopt.com>'
    assert classify(frm, "Save today", "me") is None
    assert classify(frm, "Save today", "me", keywords=["safeopt"]) == "Your keyword: safeopt"
    assert classify('"CarShield" <a@b.com>', "x", "me", use_defaults=False) is None
    assert classify(frm, "x", "me", keywords=["safeopt"], allowlist=["@safeopt.com"]) is None
    assert classify(frm, "x", "me", keywords=["safeopt"], allowlist=["promo@safeopt.com"]) is None


def test_clean_inputs():
    assert clean_keyword("Fidelity Life") == "fidelitylife"
    with pytest.raises(ValueError):
        clean_keyword("a.b")
    assert clean_allow_entry("Bob <BOB@Example.com>") == "bob@example.com"
    assert clean_allow_entry("@Example.com") == "@example.com"
    with pytest.raises(ValueError):
        clean_allow_entry("not an email")


def test_suggestions_group_variants_and_flag_coverage():
    headers = [
        {"from": '"FID.ELI.TY LI.FE" <a@x.com>', "subject": "Offer"},
        {"from": '"FiDeLity.LiFe.TeaM" <b@y.com>', "subject": "Offer"},
        {"from": '"FiDelity      Life^^^^" <c@z.com>', "subject": "Offer"},
        {"from": '"Metro via SafeOpt" <p@safeopt.com>', "subject": "Save"},
        {"from": '"Metro via SafeOpt" <p@safeopt.com>', "subject": "Save more"},
        {"from": '"Renovate AI" <sid@renovate.ai>', "subject": "Congrats"},   # seen once, not mangled
        {"from": "no-name@foo.com", "subject": "x"},
    ]
    out = build_suggestions(headers, keywords=[], use_defaults=False)
    keys = {s["keyword"]: s for s in out}
    assert keys["fidelitylife"]["count"] == 3 and keys["fidelitylife"]["mangled"]
    assert keys["metroviasafeopt"]["count"] == 2 and not keys["metroviasafeopt"]["mangled"]
    assert "renovateai" not in keys
    assert out[0]["keyword"] == "fidelitylife"  # mangled ranks first

    covered = build_suggestions(headers, use_defaults=True)
    assert {s["keyword"]: s for s in covered}["fidelitylife"]["already_caught"]


def test_access_states():
    t = now()
    trial = {"trial_ends_at": t + timedelta(days=3, hours=1)}
    assert has_access(trial) and plan_state(trial) == "trial" and trial_days_left(trial) == 4
    expired = {"trial_ends_at": t - timedelta(seconds=1)}
    assert not has_access(expired) and plan_state(expired) == "expired"
    assert has_access({**expired, "subscription_status": "active"})
    assert plan_state({**expired, "subscription_status": "past_due"}) == "past_due"
    assert not has_access({**expired, "subscription_status": "canceled"})
