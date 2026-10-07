"""Onboarding helper: look at what already landed in Spam and propose keywords.

Input is a list of {"from", "subject", "to"} header dicts. Output is a ranked list of
suggestions. Pure functions so it is easy to test; gmail.py does the fetching.
"""
import re
from collections import defaultdict
from email.utils import parseaddr

from .filtering import MIN_KEYWORD_LEN, classify, normalize

MANGLE = re.compile(r"[A-Za-z][.^_*|~`'\-][A-Za-z]|\s{3,}|\^{2,}|(?:[a-z][A-Z]){2,}[a-z]?")
MAX_KEY_LEN = 32
MIN_COUNT = 2
MAX_SUGGESTIONS = 15


def display_name(from_header: str) -> str:
    name, addr = parseaddr(from_header or "")
    return (name or "").strip().strip('"')


def looks_mangled(name: str) -> bool:
    return bool(MANGLE.search(name or ""))


def _key(name: str):
    k = normalize(name)
    if MIN_KEYWORD_LEN <= len(k) <= MAX_KEY_LEN:
        return k
    return None


def build_suggestions(headers, *, keywords=(), use_defaults=True, allowlist=()):
    groups = defaultdict(lambda: {"count": 0, "mangled": 0, "samples": [], "covered": 0})
    for h in headers:
        frm, subject, to = h.get("from", ""), h.get("subject", ""), h.get("to", "")
        name = display_name(frm)
        key = _key(name)
        if not key:
            continue
        g = groups[key]
        g["count"] += 1
        g["mangled"] += looks_mangled(name)
        if classify(frm, subject, to, keywords=keywords, use_defaults=use_defaults, allowlist=allowlist):
            g["covered"] += 1
        if len(g["samples"]) < 3:
            g["samples"].append({"from": name[:80], "subject": (subject or "")[:100]})

    # Merge variants: "fidelitylifeteam" folds into "fidelitylife" when both appear.
    keys = sorted(groups, key=len)
    for i, short in enumerate(keys):
        if short not in groups:
            continue
        for longer in keys[i + 1:]:
            if longer in groups and short in longer:
                g, other = groups[short], groups.pop(longer)
                for f in ("count", "mangled", "covered"):
                    g[f] += other[f]
                g["samples"] = (g["samples"] + other["samples"])[:3]

    out = []
    for key, g in groups.items():
        if g["count"] < MIN_COUNT and not g["mangled"]:
            continue
        out.append({
            "keyword": key,
            "count": g["count"],
            "mangled": g["mangled"] > 0,
            "already_caught": g["covered"] == g["count"],
            "samples": g["samples"],
        })
    # Mangled names first (strongest spam signal), then by volume.
    out.sort(key=lambda s: (s["already_caught"], not s["mangled"], -s["count"], s["keyword"]))
    return out[:MAX_SUGGESTIONS]
