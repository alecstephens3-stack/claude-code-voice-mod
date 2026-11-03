#!/usr/bin/env python3
"""
copy_rules.py: the rules for copy that leaves the building, the ones with a single
right answer. voice_check.py imports this module for its word lists, its HTML
stripping and its surface gates, so the rules live in one place.

Profiles:

  email     A client email. Short, warm, about one exclamation point, a greeting
            and a sign-off line.
  artifact  Anything else that goes out: an HTML page, a guide, a social post.
            Tags and inlined CSS are stripped first.

Both profiles fail on em and en dashes, the FILLER list and the BANNED words.

Claims: every number with a unit ("40 hours", "93%") that does not appear in a
proof file comes back as a VERIFY line. Proof files are the documents you keep as
the record of real work (a portfolio, case studies). Pass them with --proof, or
list them in the VOICE_PROOF_FILES environment variable (separated by the OS path
separator). With no proof files the claim check is skipped. VERIFY lines never
fail a run: phrasing varies, so a miss is a prompt to check, not proof of an error.

    python3 checker/copy_rules.py draft.txt --profile email
    cat page.html | python3 checker/copy_rules.py - --profile artifact --html
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PROFILES = ("email", "artifact")

# ---------------------------------------------------------------------------
# The word lists. Edit these to match your own house style.

FILLER = (
    "i'd be happy to", "i would be happy to", "i'd love to", "i would love to",
    "please find attached", "per my last email", "kindly", "i wanted to reach out",
    "looking forward to hearing", "let me know if you're interested",
    "let me know if you are interested",
    "leverage", "synergy", "move the needle", "circle back", "touch base",
    "cutting-edge", "unlock", "utilize", "world-class", "best-in-class",
    # "ramp" for training or learning a job reads as filler. Say training, up to speed.
    "ramp up", "ramp-up", "ramping up", "onboarding ramp",
)

# Whole-word patterns that fail outgoing copy, with the reason shown to the writer.
BANNED = (
    (re.compile(r"\b(eat|eats|eating|ate|eaten)\b"),
     "'eat' in a figurative sense ('eats time', 'eat the cost'). Say what it costs plainly"),
    (re.compile(r"\bquietly\b"),
     "'quietly' adds drama without meaning. State what happened"),
    (re.compile(r"\bquick one\b"),
     "'quick one' as an opener. Open with the point itself"),
)

GREETING = re.compile(r"^\s*(hey|hi|hello|dear)\b", re.I)
SIGN_OFF = re.compile(r"(?im)^\s*(thanks|thank you|talk soon|looking forward)[!.,]?\s*$")
EMAIL_MAX_WORDS = 100

# A number carrying a unit is a factual claim. Bare integers ("3 bullets") are not.
# The word-boundary anchor sits inside the alternatives: "93%." has no boundary
# between the percent sign and the full stop, so a trailing \b would drop every
# percentage that ends a sentence.
UNIT_WORDS = (
    "percent", "hours", "hour", "hrs", "hr", "minutes", "minute", "mins", "min",
    "days", "day", "weeks", "week", "months", "month", "years", "year",
    "pages", "page", "prices", "price", "seats", "seat", "staff", "people",
    "employees", "employee", "clients", "client", "invoices", "invoice",
)
CLAIM = re.compile(r"\b\d[\d,.]*[ \t]*(?:%|(?:" + "|".join(UNIT_WORDS) + r")\b)", re.I)

# Different words for the same thing. A claim is compared as (number, family), so
# "60-100 hrs" in a proof file covers "100 hours" in a draft.
FAMILY = {
    "%": "percent", "percent": "percent",
    "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour",
    "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute",
    "day": "day", "days": "day", "week": "week", "weeks": "week",
    "month": "month", "months": "month", "year": "year", "years": "year",
    "page": "page", "pages": "page", "price": "price", "prices": "price",
    "seat": "seat", "seats": "seat", "staff": "person", "people": "person",
    "employee": "person", "employees": "person",
    "client": "client", "clients": "client",
    "invoice": "invoice", "invoices": "invoice",
}


# ---------------------------------------------------------------------------

def words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def strip_html(raw: str) -> str:
    """Prose only. Script, style and inlined CSS carry numbers that are not
    claims, so they go first."""
    out = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", raw)
    out = re.sub(r"(?s)<!--.*?-->", " ", out)
    out = re.sub(r"(?s)<[^>]+>", " ", out)
    out = (out.replace("&nbsp;", " ").replace("&amp;", "&")
              .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
              .replace("&#39;", "'").replace("·", " "))
    return re.sub(r"[ \t]+", " ", out)


def proof_files(extra: list[str] | None = None) -> list[Path]:
    names = list(extra or [])
    env = os.environ.get("VOICE_PROOF_FILES", "")
    names += [n for n in env.split(os.pathsep) if n.strip()]
    return [Path(n) for n in names if Path(n).is_file()]


def as_pair(claim: str) -> tuple[str, str] | None:
    """Reduce '60 hrs' and '60 hours' to the same (number, family) pair."""
    s = claim.lower().strip().replace(",", "")
    m = re.match(r"(\d[\d.]*)\s*(%|[a-z]+)", s)
    if not m:
        return None
    family = FAMILY.get(m.group(2))
    return (m.group(1).rstrip("."), family) if family else None


def corpus_pairs(corpus: str) -> set[tuple[str, str]]:
    return {p for m in CLAIM.finditer(corpus) if (p := as_pair(m.group(0)))}


def shared_checks(text: str, profile: str, proof: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Every profile: dashes, filler, banned words, and claims against the proof files.
    Each FAIL line starts with its code (DASH, FILLER, BANNED)."""
    hard: list[str] = []
    verify: list[str] = []
    low = text.lower()

    for ch, name in (("—", "em dash"), ("–", "en dash")):
        if ch in text:
            hard.append(f"DASH      contains a {name}. Periods, commas or colons instead")
    for f in FILLER:
        if f in low:
            hard.append(f"FILLER    contains {f!r}")
    for rx, why in BANNED:
        for m in rx.finditer(low):
            hard.append(f"BANNED    contains {m.group(0)!r}: {why}")

    files = proof_files(proof)
    if files:
        known = corpus_pairs("\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in files).lower())
        seen: set[tuple[str, str]] = set()
        for m in CLAIM.finditer(text):
            pair = as_pair(m.group(0))
            if not pair or pair in seen:
                continue
            seen.add(pair)
            if pair not in known:
                verify.append(f"PROOF     {m.group(0)!r} does not appear in the proof files. Check it or cut it")
    return hard, verify


def email_checks(text: str) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    verify: list[str] = []
    bangs = text.count("!")
    if bangs > 2:
        hard.append(f"WARMTH    {bangs} exclamation points. Aim for about one per email")
    elif bangs == 2:
        verify.append("WARMTH    2 exclamation points. One is the target; check the second is earned")
    if not GREETING.match(text):
        verify.append('OPENING   no greeting line. Client emails open "Hey <Name>," or "Hi <Name>,"')
    if not SIGN_OFF.search(text):
        verify.append('CLOSE     no sign-off line found. "Thanks" or "Talk soon," on its own line')
    n = words(text)
    if n > EMAIL_MAX_WORDS:
        hard.append(f"LENGTH    {n} words. Client emails stay under {EMAIL_MAX_WORDS}: what changed, what they do, stop")
    return hard, verify


def check(text: str, profile: str, proof: list[str] | None = None) -> tuple[list[str], list[str]]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    hard, verify = shared_checks(text, profile, proof)
    if profile == "email":
        h, v = email_checks(text)
        hard, verify = hard + h, verify + v
    return hard, verify


def main() -> int:
    ap = argparse.ArgumentParser(description="Check outgoing copy against the rules with one right answer.")
    ap.add_argument("file", help="path to the draft, or - for stdin")
    ap.add_argument("--profile", required=True, choices=PROFILES)
    ap.add_argument("--html", action="store_true", help="strip HTML first")
    ap.add_argument("--proof", action="append", default=[], help="a proof file (repeatable)")
    a = ap.parse_args()
    raw = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
    is_html = a.html or (a.file != "-" and Path(a.file).suffix.lower() in (".html", ".htm"))
    text = strip_html(raw) if is_html else raw
    hard, verify = check(text, a.profile, a.proof)
    print(f"copy_rules · {a.profile} · {words(text)} words · {len(proof_files(a.proof))} proof file(s)")
    for line in hard:
        print("  FAIL   " + line)
    for line in verify:
        print("  VERIFY " + line)
    if not hard and not verify:
        print("  clean.")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
