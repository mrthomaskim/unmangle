"""Spam rules. Only the From, To and Subject headers are ever inspected."""
import re
import unicodedata
from email.utils import parseaddr

DIGIT_MAP = str.maketrans({"1": "i", "0": "o", "3": "e", "5": "s", "4": "a", "7": "t"})

# Built-in rules users can switch off. Written already normalized.
DEFAULT_KEYWORDS = [
    "carshield",
    "endurance",
    "autoplanadvisors",
    "extendedwarranty",
    "vehicleprotection",
    "fidelitylife",
]

BROKEN_TO = re.compile(r"@gmail\.com@|random_anm", re.I)
LTD_SEGMENT = re.compile(r"(?=\.Ltd\.)", re.I)  # lookahead: counts '.Ltd.' even when segments share a dot

MIN_KEYWORD_LEN = 4
MAX_KEYWORDS = 200
MAX_ALLOWLIST = 500


def normalize(s: str) -> str:
    """'FiD.ELI-TY_L1FE 🎉' -> 'fidelitylife'. Strips accents, punctuation, spacing, emoji."""
    s = unicodedata.normalize("NFKD", s or "").lower().translate(DIGIT_MAP)
    return re.sub(r"[^a-z]", "", s)


def clean_keyword(raw: str):
    """Returns the stored form of a user keyword, or raises ValueError with a user-facing message."""
    k = normalize(raw)
    if len(k) < MIN_KEYWORD_LEN:
        raise ValueError(f"Keywords need at least {MIN_KEYWORD_LEN} letters after removing "
                         "spaces, dots and symbols, so they don't catch real mail by accident.")
    return k


def clean_allow_entry(raw: str):
    """Accepts 'person@site.com' or '@site.com'. Returns the lowercase form or raises ValueError."""
    s = (raw or "").strip().lower()
    if s.startswith("@") and re.fullmatch(r"@[a-z0-9.-]+\.[a-z]{2,}", s):
        return s
    addr = parseaddr(s)[1]
    if re.fullmatch(r"[^@\s]+@[a-z0-9.-]+\.[a-z]{2,}", addr):
        return addr
    raise ValueError("Enter an email address like name@example.com, or a whole domain like @example.com.")


def sender_address(from_header: str) -> str:
    return (parseaddr(from_header or "")[1] or "").lower()


def is_allowed(from_header: str, allowlist) -> bool:
    addr = sender_address(from_header)
    if not addr:
        return False
    domain = "@" + addr.split("@")[-1]
    return any(a == addr or a == domain for a in (allowlist or []))


def classify(frm: str, subject: str, to: str, *, keywords=(), use_defaults=True, allowlist=()):
    """Returns a short reason string if the message is spam, else None."""
    if is_allowed(frm, allowlist):
        return None
    text = normalize(f"{frm} {subject}")
    for k in keywords or ():
        if k and k in text:
            return f"Your keyword: {k}"
    if use_defaults:
        for k in DEFAULT_KEYWORDS:
            if k in text:
                return f"Built-in: {k}"
        if BROKEN_TO.search(to or ""):
            return "Built-in: forged recipient"
        if len(LTD_SEGMENT.findall(sender_address(frm))) >= 2:
            return "Built-in: junk sender address"
    return None
