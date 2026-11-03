#!/usr/bin/env python3
"""
voice_check.py: the deterministic writing check behind the voice mod.

The mod runs it after every chat reply and before any draft or client-facing file
is written. The measuring stick is ASD-STE100 Simplified Technical English (Issue
9): strict on steps (checklists, how-to guides), a light touch everywhere else.
Word lists, dashes, the claim check and HTML stripping come from copy_rules.py,
imported, not copied. Standard library only.

What it checks:

  FAIL   chat: PATH (local paths, non-web link targets), HASH (git hashes,
         UUIDs, tool and agent ids), COMMAND (shell fences, "$ " lines), OPENER
         (Certainly / Absolutely / Great question / Sure thing / I'd be happy
         to), BANNED (copy_rules FILLER and BANNED, bare "ramp"), DRAFT (dash,
         filler or banned words inside outgoing copy quoted in the reply).
         email/artifact/social/guide: copy_rules FAIL lines for that profile.
         guide and steps: every step that breaks a strict STE rule; guide: PARA6.
  NOTE   chat: FORMAT, QUESTIONS, PARA6. Any surface: ING, THISVERB, LATIN.
  STE    per sentence. LONG (steps over 20 words, prose over 25, counted with
         STE rule 8), PASSIVE, SEMICOLON; steps also MULTI (two instructions)
         and CONDITION (condition after the command). Scores: share passing.

What it cannot check: whether a sentence is clear; whether a passive is the
"actor unknown" kind STE allows in description; word meaning or part of speech
(no ASD dictionary, rules 1.1 to 1.13); multi-word nouns (STE 2.1 needs a
part-of-speech tagger); whether a question is really meant for the reader;
whether the right fact was chosen. Contractions are never flagged: a warm
house voice beats STE 4.2.

Judgment calls beyond the plain STE rules, each one added after a false alarm or
a miss on real replies:
  - DRAFT also covers unfenced drafts: a blockquote, or a section between "---"
    rules, that opens with a greeting or "Subject:". Models rarely fence drafts.
  - Chat: a numbered item is a step only when it opens with an imperative
    (findings get numbered as often as instructions). Other surfaces treat every
    list item as a step. "If it asks, click Replace." opens with a condition
    and is a step. In a step item, later sentences are steps only if imperative.
  - Chat: blockquotes that are not drafts are someone else's words, so they are
    not STE-scored and not checked for PATH, HASH or BANNED. Double-quoted text
    is a mention, not the writer's voice, for PATH, HASH, BANNED and QUESTIONS.
  - PATH: mailto:, tel: and #anchor links are fine; bare file names are fine.
  - PASSIVE: present tense + participle is a state ("Everything is saved and
    pushed") unless an agent or process word follows, a frequency adverb
    precedes, or a subordinator opens the clause ("before it's sent").
  - MULTI: a bold name after click/press/select ("Click **Save and check the
    connection**") is one label (STE 8.6), not a second instruction.
  - CONDITION: skips "if" meaning whether ("ask her if"), "until" ending a loop
    ("click it again until", "wait until"), and "once" as an adverb.
  - FORMAT: a list is fine when it holds steps, short data rows, or sits under
    two or more sentences of prose. QUESTIONS skips quoted lines, bold titles
    and prepared question lists ("**Questions to bring to the call:**").
  - HTML: an element with class="step" counts as a step like <ol><li>;
    <b>/<strong> keep their bold.
  - PRODUCT_NAMES ("Select Optical", "Open Dental") are names, not orders.
    Add your own when one misfires.

    python3 checker/voice_check.py reply.txt --surface chat
    cat draft.txt | python3 checker/voice_check.py - --surface email --json
    python3 checker/voice_check.py guide.html --surface guide

Exit 0 = ok (no FAIL). Exit 1 = at least one FAIL. NOTE, VERIFY and STE lines on
a report-only surface never fail the run on their own.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import copy_rules  # noqa: E402  (path set on the line above, by design)

SURFACES = ("chat", "email", "guide", "artifact", "social", "steps")

# copy_rules profile whose FAIL lines gate each surface. Chat and steps have
# their own fail rules and borrow only pieces of copy_rules.
PROFILE = {"email": "email", "guide": "artifact", "artifact": "artifact", "social": "artifact"}

PROSE_TARGET = 0.80
STEPS_TARGET = 1.00
PROSE_MAX = 25   # STE 6.3, descriptive sentences
STEPS_MAX = 20   # STE 5.1, instructions
PARA_MAX = 6     # STE 6.6, sentences per paragraph

# --------------------------------------------------------------------------
# Imperatives. Generous on purpose: a missed step costs a strict check, a
# false step costs only the 20-word cap. Multi-word entries match first.
IMPERATIVES = (
    "open", "click", "press", "select", "choose", "pick", "type", "enter", "close",
    "save", "check", "make sure", "go", "find", "move", "put", "set", "turn", "run",
    "wait", "send", "reply", "add", "remove", "delete", "copy", "paste", "sign",
    "log", "scan", "print", "file", "drag", "drop", "start", "stop", "restart",
    "update", "install", "read", "write", "ask", "tell", "call", "book", "review",
    "confirm", "tick", "mark", "keep", "leave", "use", "follow", "sign in", "upload",
    "download", "attach", "forward", "share", "accept", "approve", "decline",
    "enable", "disable", "connect", "test", "try", "look", "note", "write down",
    "bring", "show", "give", "let", "email", "text", "message",
    # Additions from the same family, found on real replies.
    "sign out", "log in", "log out", "create", "edit", "rename", "replace",
    "reload", "refresh", "reconnect", "fill in", "fill", "submit", "tap", "scroll",
    "switch", "visit", "watch", "search", "unplug", "plug", "skip", "hide",
    "make", "fix", "change", "pay", "respond", "merge", "push", "publish",
    "schedule", "invite", "drop", "plan", "double-check", "verify", "take", "get",
    "hand", "grab", "hold", "swap", "cancel", "undo", "approve",
)
_IMP_SORTED = sorted(set(IMPERATIVES), key=lambda w: -len(w))
_IMP_RX = re.compile(r"(?:" + "|".join(re.escape(w) for w in _IMP_SORTED) + r")(?![\w-])", re.I)

# Names that start with an imperative but are not orders. Extend this when a
# product name in your own work misfires.
PRODUCT_NAMES = {"select optical", "open dental", "print station"}

# Verbs that take no object, so "... and wait." is still a second instruction.
INTRANSITIVE = {"wait", "go", "restart", "reply", "try", "stop", "look", "sign in",
                "log in", "log out", "sign out", "refresh", "reload", "respond",
                "start", "close", "save", "scroll", "leave"}

# A word right after a candidate verb that proves it was a noun ("Draft is in
# your Gmail", "Call with Dana: Thursday", "Review of the claims").
AUX = {"is", "are", "was", "were", "has", "have", "had", "will", "would", "can",
       "could", "should", "does", "did", "isn't", "aren't", "wasn't", "won't",
       "of", "goes", "went", "runs", "ran", "now", "lands", "came", "comes",
       "said", "says", "asks", "asked", "needs", "works", "worked", "looks",
       "showed", "shows", "covers", "takes", "took", "keeps"}

# Words that can follow an imperative and start its object or complement.
OBJECT_START = {"the", "a", "an", "your", "her", "his", "their", "our", "my", "its",
                "this", "that", "these", "those", "it", "them", "him", "me", "us",
                "each", "every", "all", "any", "both", "one", "on", "off", "in",
                "out", "up", "down", "back", "over", "through", "to", "into",
                "for", "with", "from", "at", "anything", "everything", "nothing",
                "something", "whatever", "what", "which", "who", "whether", "how",
                "if", "when", "only", "once", "again", "yourself", "here", "there",
                "now", "first", "next", "sure", "some", "two", "three", "another"}

# --------------------------------------------------------------------------
# Passive voice. STE 3.6 wants active voice; STE 3.3 allows a past participle
# used as an adjective, so status words ("it's fixed", "the call is done") are
# allowlisted. The contract's list first, then additions the product check
# showed are almost always states in status replies.
PASSIVE_ALLOW = {
    "closed", "opened", "based", "located", "finished", "signed", "attached", "done",
    "fixed", "installed", "connected", "set up", "logged in",
    # additions: adjective, naming and modal-like uses
    "supposed", "interested", "worried", "excited", "pleased", "tired", "concerned",
    "married", "called", "named", "titled", "labelled", "labeled", "updated",
    "gone", "used to", "related", "mixed", "stuck", "set", "worth", "meant",
    "allowed", "limited", "tied", "built in", "broken",
}
IRREGULAR_PP = {
    "sent", "made", "given", "taken", "shown", "seen", "written", "put", "kept",
    "held", "told", "found", "brought", "bought", "caught", "taught", "thought", "run",
    "read", "cut", "chosen", "broken", "spoken", "driven", "forgotten", "begun",
    "drawn", "grown", "thrown", "worn", "torn", "sold", "won", "lost", "spent",
    "lent", "dealt", "felt", "fed", "led", "met", "shot", "shut", "split", "spread",
    "struck", "swept", "understood", "withdrawn", "mistaken", "rewritten", "undone",
    "redone", "hit", "sung", "rung", "stolen", "hung", "overwritten", "bound",
    "ground", "laid", "paid", "said", "heard", "sought", "upheld",
} | {w for w in PASSIVE_ALLOW if " " not in w}
NOT_PARTICIPLE = {"red", "bed", "need", "feed", "seed", "speed", "shed", "wed",
                  "indeed", "hundred", "sacred", "naked", "wicked", "rugged",
                  "ragged", "embed", "proceed", "succeed", "exceed", "breed",
                  "greed", "weed", "reed", "deed", "heed", "steed", "shred"}
BE_FORMS = (r"am|is|are|was|were|be|been|being|isn't|aren't|wasn't|weren't|"
            r"(?:it|that|what|there|here|he|she|who|everything|nothing|this)['\u2019]s|"
            r"(?:they|we|you)['\u2019]re")
ADVERB = (r"(?:\w+ly|not|never|already|now|also|still|just|all|both|then|only|"
          r"usually|always|often|really|even|probably|actually|yet|automatically|"
          r"currently|properly|correctly|being)")
_PASSIVE_RX = re.compile(
    r"\b(?P<be>" + BE_FORMS + r")\s+(?P<adv>(?:" + ADVERB + r"\s+){0,2})(?P<pp>[A-Za-z]+)(?P<rest>\s+\w+)?",
    re.I)
# After a contracted "'s", a participle followed by an object is "has" + verb,
# active voice: "she's sent the email".
OBJECTISH = {"the", "a", "an", "her", "his", "their", "its", "my", "your", "our",
             "this", "that", "these", "those", "two", "three", "some", "all",
             "every", "it", "them", "me", "us", "him"}
# Present tense + participle is usually a state in a status reply ("Everything
# is saved and pushed.", "The guide is untouched.", "version 34 is published"):
# STE 3.3 allows the participle as an adjective. It reads as an action only with
# one of these signs: an agent or a process complement after it, a frequency
# adverb before it, or a subordinator just before the subject ("as it's
# spoken", "before any caption is drawn"). Past and non-finite forms (was,
# were, be, been, being) are events and need no sign. Without this rule most
# PASSIVE hits on status replies were states.
PRESENT_BE = re.compile(r"^(?:am|is|are|isn't|aren't|\w+['\u2019](?:s|re))$", re.I)
ACTION_AFTER = {"by", "from", "to", "into", "onto", "with", "through", "via", "for",
                "every", "each", "whenever", "automatically", "daily", "weekly",
                "nightly", "monthly"}
# Scheduling words are states in the present tense even with "for": "nothing
# is booked for today", "the call is scheduled for Tuesday".
STATE_PRESENT = {"booked", "scheduled", "planned", "slated", "reserved", "due"}
ACTION_ADVERB = {"always", "never", "usually", "automatically", "often", "sometimes",
                 "regularly", "rarely", "typically", "normally", "generally", "being"}
ACTION_BEFORE = {"as", "when", "whenever", "before", "after", "until", "once", "if",
                 "unless", "while"}

# --------------------------------------------------------------------------
# Conditions and soft notes.
SUBORDINATORS = ("if", "when", "unless", "once", "until", "before", "after")
PRONOUN_SUBJECT = {"it", "she", "he", "they", "you", "we", "i", "there", "that",
                   "this", "everything", "nothing", "someone", "anyone"}
FINITE = {"is", "are", "was", "were", "has", "have", "had", "does", "do", "did",
          "can", "will", "would", "should", "could", "may", "might", "must",
          "isn't", "aren't", "doesn't", "don't", "won't", "can't", "didn't"}
WHETHER_VERBS = {"ask", "asks", "asking", "check", "see", "find", "know", "wonder",
                 "sure", "confirm", "decide", "tell", "whether"}
DETERMINER = {"the", "a", "an", "your", "her", "his", "their", "our", "my", "its",
              "this", "that", "each", "every", "any", "no"}

ING_RX = re.compile(r",\s+(making|allowing|ensuring|enabling|letting|giving|helping|"
                    r"keeping|resulting|creating|providing|meaning|leaving|saving|"
                    r"freeing|turning|bringing)\b", re.I)
THISVERB_RX = re.compile(
    r"^(?:\W*)this\s+(is|was|will|would|can|could|should|may|might|must|has|had|"
    r"does|did|isn['\u2019]t|wasn['\u2019]t|won['\u2019]t|doesn['\u2019]t|means|makes|"
    r"lets|gives|keeps|shows|works|helps|covers|saves|fixes|matters|happens|needs|"
    r"takes|puts|sends|adds|removes|changes|stops|runs|checks|tells|leaves|"
    r"explains|ensures|allows|creates|includes|applies|goes|comes|brings|turns)\b", re.I)
LATIN_RX = re.compile(r"(?<!\w)(e\.g\.|i\.e\.|etc\.?)(?!\w)", re.I)

# --------------------------------------------------------------------------
# Chat fail patterns: what a reader who does not read code should never see.
URL_RX = re.compile(r"\b(?:https?|ftp)://[^\s)>\]]+", re.I)
DOMAIN_RX = re.compile(
    r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|co|app|io|ai|dev|org|net|tech|jp|so|me|sh|us|"
    r"video|page|link|site)\b(?:/[^\s)>\]]*)?", re.I)
ABS_PATH_RX = re.compile(
    r"(?<![\w/.:~])(?:~/[^\s`'\")\]]+|/(?:Users|private|tmp|var|opt|etc|Applications|"
    r"Library|Volumes|System|usr|home)/[^\s`'\")\]]*)")
REL_PATH_RX = re.compile(
    r"(?<![\w/.:@-])(?:\.{1,2}/)?[\w.@-]+(?:/[\w.@ -]*[\w@-])*/[\w@-][\w.@-]*\.[A-Za-z][A-Za-z0-9]{0,5}\b")
MD_LINK_RX = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
SAFE_SCHEMES = re.compile(r"^(?:https?|mailto|tel):|^#", re.I)
HEX_RX = re.compile(r"(?<![\w-])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}(?![\w-])")
UUID_RX = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
TOOLID_RX = re.compile(r"\b(?:srvtoolu|toolu|msg|bash)_[A-Za-z0-9]{4,}\b|\bagent_?[Ii]d\b")
DOLLAR_CMD_RX = re.compile(r"(?m)^\s*\$ (?!\d)\S")
COMMAND_LANGS = {"bash", "sh", "zsh", "shell", "console"}
DRAFT_LANGS = {"", "text", "email", "markdown", "md"}
OPENER_RX = re.compile(
    r"^\W*(certainly|absolutely|great question|sure thing|"
    r"i['\u2019]d be happy to|i would be happy to)\b", re.I)
RAMP_RX = re.compile(r"\bramps?\b(?![- ]up)", re.I)
GREETING_RX = re.compile(r"^\s*(hey|hi|hello|dear)\b[^\n.!?]{0,40}[,!]?\s*$", re.I)
GREETING_START_RX = re.compile(r"^\s*(?:(?:hey|hi|hello|dear)\b[\w.' -]{0,30}[,!]|subject:)", re.I)
# The names emails are signed with, so a sign-off line is not scored as a sentence.
SIGN_OFF_NAMES = ("sam",)
SALUTATION_RX = re.compile(
    r"^\s*(hope (you're|you are|you|all|your)\b.{0,40}|thanks[!.]?|thank you[!.]?|"
    r"talk soon[!.,]?|" + "|".join(re.escape(n) for n in SIGN_OFF_NAMES) + r")\s*$", re.I)

BULLET_RX = re.compile(r"^\s*[-*+\u2022]\s+")
CHECK_RX = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+")
NUMBER_RX = re.compile(r"^\s*\d{1,3}[.)]\s+")
HEADING_RX = re.compile(r"^\s{0,3}#{1,6}\s+")
LABEL_LINE_RX = re.compile(r"^\s*(\*\*|__)[^*_]+(\*\*|__):?\s*$")
LEAD_LABEL_RX = re.compile(r"^\s*(?:\*\*|__)([^*_]{1,60}?)(?::\s*(?:\*\*|__)|(?:\*\*|__)\s*:)\s*")
HR_RX = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
FENCE_RX = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)")


# ==========================================================================
# Parsing: text -> blocks

class Block:
    """One unit of the reply: a paragraph, a list item, a heading, a fence..."""
    __slots__ = ("kind", "text", "lang", "quote", "draft", "step")

    def __init__(self, kind, text="", lang="", quote=False):
        self.kind, self.text, self.lang, self.quote = kind, text, lang, quote
        self.draft = False   # outgoing copy inside a chat reply
        self.step = False    # an instruction (list item kinds only)

    def __repr__(self):  # debugging aid
        return f"Block({self.kind!r}, {self.text[:40]!r}, step={self.step})"


def parse(text: str) -> list[Block]:
    """Split markdown-ish text into blocks. Not a full markdown parser: enough
    to tell prose from list items, code, tables and headings."""
    blocks: list[Block] = []
    lines = text.splitlines()
    i = 0
    open_para: Block | None = None
    while i < len(lines):
        raw = lines[i]
        m = FENCE_RX.match(raw)
        if m:
            fence, lang = m.group(1), m.group(2).lower()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence[0] * 3):
                body.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(body), lang))
            open_para = None
            i += 1
            continue
        i += 1
        quote = False
        line = raw
        if re.match(r"^\s*>", line):
            quote = True
            line = re.sub(r"^\s*>\s?", "", line)
        if not line.strip():
            open_para = None
            continue
        if HR_RX.match(line):
            blocks.append(Block("hr", "", quote=quote))
            open_para = None
            continue
        if HEADING_RX.match(line) or LABEL_LINE_RX.match(line):
            blocks.append(Block("heading", HEADING_RX.sub("", line).strip(), quote=quote))
            open_para = None
            continue
        if line.lstrip().startswith("|"):
            blocks.append(Block("table", line, quote=quote))
            open_para = None
            continue
        for kind, rx in (("check", CHECK_RX), ("number", NUMBER_RX), ("bullet", BULLET_RX)):
            if rx.match(line):
                b = Block(kind, rx.sub("", line, count=1).strip(), quote=quote)
                blocks.append(b)
                open_para = b
                break
        else:
            if open_para is not None and open_para.quote == quote:
                open_para.text += " " + line.strip()
            else:
                open_para = Block("para", line.strip(), quote=quote)
                blocks.append(open_para)
    return blocks


_STEP_CLASS_RX = re.compile(r"""\bclass\s*=\s*["'](?:[^"']*\s)?step(?:\s[^"']*)?["']""", re.I)
_BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "main", "blockquote",
               "table", "figure", "figcaption", "dl", "dt", "dd", "aside", "details",
               "summary", "form", "fieldset", "body"}


def html_to_text(raw: str) -> str:
    """HTML -> the markdown-ish shape parse() reads, then copy_rules.strip_html
    for the actual tag stripping. Keeps the step structure: <ol><li> become
    numbered items, a <li> holding a checkbox becomes "- [ ]", and the first
    paragraph of a class="step" element (a step card in a page template)
    becomes a numbered item too."""
    s = re.sub(r"(?is)<(head|nav|button|template|noscript)\b.*?</\1>", " ", raw)
    s = re.sub(r"(?s)<!--.*?-->", " ", s)
    s = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", s)
    # Bold and key names keep their markdown form, so a button name after
    # "Click" counts as one word (STE 8.6) exactly as it does in chat.
    s = re.sub(r"(?i)</?(?:b|strong|kbd)\b[^>]*>", "**", s)
    s = re.sub(r"(?is)<pre\b[^>]*>(.*?)</pre>",
               lambda m: "\n```code\n" + re.sub(r"<[^>]+>", "", m.group(1)) + "\n```\n", s)
    out: list[str] = []
    stack: list[str] = []
    pos = 0
    item_open = False   # a list marker was written and no text has followed yet
    kit_step = False    # inside a class="step" element, waiting for its <p>
    for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", s):
        between = s[pos:m.start()]
        out.append(between)
        if between.strip():
            item_open = False
        pos = m.end()
        close, tag = m.group(1), m.group(2).lower()
        if not close and _STEP_CLASS_RX.search(m.group(0)):
            kit_step = True
        if tag in ("ol", "ul"):
            if close:
                if stack:
                    stack.pop()
            else:
                stack.append(tag)
            out.append("\n\n")
            item_open = False
        elif tag == "li" and not close:
            nxt = re.search(r"(?is)</li>|<li\b|<ul\b|<ol\b", s[pos:])
            chunk = s[pos:pos + (nxt.start() if nxt else len(s))]
            if re.search(r"(?i)<input[^>]*type=[\"']?checkbox", chunk):
                out.append("\n- [ ] ")
            elif stack and stack[-1] == "ol":
                out.append("\n1. ")
            else:
                out.append("\n- ")
            item_open = True
        elif tag == "p" and not close and kit_step:
            out.append("\n\n1. ")
            kit_step, item_open = False, True
        elif re.fullmatch(r"h[1-6]", tag):
            out.append("\n\n" if close else "\n\n# ")
            item_open = False
        elif tag == "tr" and not close:
            out.append("\n| ")
        elif tag in ("td", "th") and not close:
            out.append(" | ")
        elif tag in _BLOCK_TAGS and not item_open:
            out.append("\n\n")
        elif tag == "br":
            out.append("\n")
        out.append(m.group(0))
    out.append(s[pos:])
    text = copy_rules.strip_html("".join(out))
    return "\n".join(line.strip() for line in text.splitlines())


# ==========================================================================
# Sentences and STE word counting

_ABBREV_END = re.compile(r"(?:\b(?:Dr|Mr|Mrs|Ms|St|Jr|Sr|vs|No|approx|Fig|Inc|Ltd|Co)\.|"
                         r"\be\.g\.|\bi\.e\.|\b[A-Z]\.)$")
_SPLIT_RX = re.compile(r"(?<=[.!?])[\"\u201d\u2019)*_]*\s+(?=[\"\u201c\u2018(*_\[]*[A-Z0-9$])")


def _inside_quote(cur: str, rest: str) -> bool:
    """Is this split point inside a quotation that closes later? A quoted
    question or a quoted email line is one unit (STE 8.6), not two sentences.
    An unbalanced quote never swallows the rest of the paragraph: the quote
    must close within 400 characters."""
    if cur.count('"') % 2 == 1 and '"' in rest[:400]:
        return True
    return cur.count("\u201c") > cur.count("\u201d") and "\u201d" in rest[:400]


def split_sentences(text: str) -> list[str]:
    """Split on . ! ? followed by a capital, keeping Dr. / e.g. / U.S. and
    quoted sentences intact."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts, cur = [], ""
    last = 0
    for m in _SPLIT_RX.finditer(text):
        chunk = text[last:m.start()]
        cur = (cur + " " + chunk).strip() if cur else chunk.strip()
        last = m.end()
        if _ABBREV_END.search(cur) or _inside_quote(cur + text[m.start():m.end()], text[m.end():]):
            cur += text[m.start():m.end()].rstrip()
            continue
        parts.append(cur + text[m.start():m.end()].rstrip())
        cur = ""
    tail = text[last:].strip()
    cur = (cur + " " + tail).strip() if cur else tail
    if cur:
        parts.append(cur)
    return [p for p in parts if p]


# A bold label right after a UI verb is a button or menu name: one word (STE
# 8.6, titles and labels), and never a second instruction. "Click **Save and
# check the connection**" was 5 of 64 MULTI hits before this.
_UI_LABEL_RX = re.compile(
    r"\b(click|press|select|choose|tap|pick|open|type|enter|drag|hit|find|use)\s+"
    r"((?:the\s+|on\s+(?:the\s+)?)?)(\*\*|__)[^*_]{1,60}\3", re.I)


def ui_labels(sentence: str) -> str:
    return _UI_LABEL_RX.sub(lambda m: f"{m.group(1)} {m.group(2)}Label", sentence)


def plain(sentence: str) -> str:
    """Markdown to plain words: link text kept, bold markers and labels gone."""
    s = MD_LINK_RX.sub(lambda m: m.group(1), sentence)
    s = LEAD_LABEL_RX.sub("", s)
    s = re.sub(r"(\*\*|__|~~)", "", s)
    s = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", s)
    return s.strip()


_UNITS = set(copy_rules.UNIT_WORDS) | {
    "am", "pm", "a.m.", "p.m.", "kb", "mb", "gb", "tb", "px", "ms", "s", "sec", "secs",
    "seconds", "second", "km", "mi", "kg", "lb", "lbs", "usd", "cst", "gmt", "bst",
    "et", "ct", "pt", "utc", "words", "lines", "files", "emails", "leads", "bills",
    "replies", "inboxes", "x", "k", "m", "dollars", "euros"}
_MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
           r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_NUM_UNIT_RX = re.compile(
    r"(?<![\w$])[$\u00a5\u20ac\u00a3]?\d[\d,.:/]*(?:\s?(?:" +
    "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True)) +
    r")\b|%)", re.I)
_DATE_RX = re.compile(r"\b\d{1,2}\s+" + _MONTHS + r"(?:\s+\d{4})?\b|\b" + _MONTHS +
                      r"\s+\d{1,2}(?:,?\s+\d{4})?\b", re.I)


def ste_words(sentence: str) -> int:
    """Word count under STE rule 8: a hyphenated word, a number with its unit,
    anything in parentheses, quoted text, a URL, inline code, and a run of
    Capitalized words not at sentence start each count as one word."""
    s = plain(ui_labels(sentence))
    s = URL_RX.sub(" W ", s)
    s = re.sub(r"`[^`]*`", " W ", s)
    for _ in range(2):  # nested parentheses
        s = re.sub(r"\([^()]*\)", " W ", s)
    s = re.sub(r"\"[^\"]*\"|\u201c[^\u201d]*\u201d", " W ", s)
    s = re.sub(r"(?<![\w])['\u2018][^'\u2018\u2019]+['\u2019](?![\w])", " W ", s)
    s = _DATE_RX.sub(" W ", s)
    s = _NUM_UNIT_RX.sub(" W ", s)
    tokens = [t for t in s.split() if re.search(r"[A-Za-z0-9]", t)]
    count, prev_cap = 0, False
    for idx, tok in enumerate(tokens):
        core = tok.strip("\"'()[]*_")
        cap = (idx > 0 and core[:1].isupper() and core not in ("I", "W")
               and not core.startswith(("I'", "I\u2019")))
        if cap and prev_cap:
            # part of a name run ("Riverside Family Dental"), already counted
            prev_cap = not re.search(r"[,;:.!?)]$", tok)
            continue
        count += 1
        prev_cap = cap and not re.search(r"[,;:.!?)]$", tok)
    return count


# ==========================================================================
# STE rules on one sentence

def _scrub(sentence: str) -> str:
    """Remove the parts rules should not read: quotes, parentheses, code, URLs."""
    s = plain(ui_labels(sentence))
    s = URL_RX.sub(" ", s)
    s = re.sub(r"`[^`]*`", " X ", s)
    s = re.sub(r"\([^()]*\)", " ", s)
    s = re.sub(r"\"[^\"]*\"|\u201c[^\u201d]*\u201d", " X ", s)
    s = re.sub(r"(?<![\w])['\u2018][^'\u2018\u2019]+['\u2019](?![\w])", " X ", s)
    return re.sub(r"\s+", " ", s).strip()


def _words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z][\w'\u2019-]*|\d[\d,.]*|[,;:.!?]", s)


def imperative_at(words: list[str], i: int, allow_bare: bool = False) -> str | None:
    """The imperative verb starting at words[i], or None when it reads as a noun.

    allow_bare: at the start of a sentence any following word will do ("Click
    OK.", "Submit."). After "and" / "then" the verb needs an object or must be
    intransitive, because "the invoice and copy" is a noun list."""
    rest = " ".join(words[i:i + 3]).lower()
    m = _IMP_RX.match(rest)
    if not m:
        return None
    verb = m.group(0).lower()
    n = len(verb.split())
    tok = words[i]
    nxt = words[i + n] if i + n < len(words) else ""
    after = nxt.lower()
    after2 = words[i + n + 1].lower() if i + n + 1 < len(words) else ""
    if len(tok) > 1 and tok.isupper():
        return None  # "INSTALL.md", "TEST": a name, not an order
    if after in AUX or after == ":" or f"{verb} {after}" in PRODUCT_NAMES and nxt[:1].isupper():
        return None  # "Draft is ready", "File Invoices" (the product)
    if after2 in AUX and after.isalpha() and after not in OBJECT_START:
        return None  # "Open tracking is off", "File Invoices is installed"
    if allow_bare:
        return verb
    if after in ("", ",", ".", "!", "?", ";"):
        return verb if verb in INTRANSITIVE else None
    if after in OBJECT_START or nxt[:1].isupper() or nxt[:1].isdigit():
        return verb
    return verb if after.endswith("ly") else None


def opens_imperative(sentence: str, condition_first: bool = True) -> bool:
    """Does this sentence give an order? "Click Close.", "Then click Close.",
    "Don't delete it.", and (condition_first) the STE shape "If it asks,
    click Replace."."""
    words = _words(_scrub(sentence))
    if not words:
        return False
    i = 0
    if words[0].lower() in SUBORDINATORS:
        if not condition_first or "," not in words[:16]:
            return False
        i = words.index(",") + 1  # condition first, then the command
    elif words[0].lower() in ("then", "next", "now", "finally", "first", "please", "also", "and"):
        i = 1
        if i < len(words) and words[i] == ",":
            i += 1
    if i < len(words) and words[i].lower() in ("don't", "don\u2019t", "never"):
        i += 1
    elif i + 1 < len(words) and words[i].lower() == "do" and words[i + 1].lower() == "not":
        i += 2
    return i < len(words) and imperative_at(words, i, allow_bare=True) is not None


def _has_clause(words: list[str]) -> bool:
    """Do these words, after a subordinator, start a clause (subject + verb)?"""
    if not words:
        return False
    w0 = words[0].lower()
    if w0 in PRONOUN_SUBJECT:
        return True
    window = [w.lower() for w in words[:6]]
    if any(w in FINITE for w in window):
        return True
    # determiner + noun + verb ("when the window stays open"), or Name + verb
    for j in range(min(4, len(words) - 1)):
        w, nxt = words[j], words[j + 1].lower()
        if (w.lower() in DETERMINER or (w[:1].isupper() and j == 0)) and j + 2 < len(words):
            v = words[j + 2].lower()
            if re.fullmatch(r"[a-z]+(?:s|ed)", v) and not v.endswith(("ss", "us", "is")):
                return True
        if w[:1].isupper() and j == 0 and re.fullmatch(r"[a-z]+(?:s|ed)", nxt) and not nxt.endswith(("ss", "us", "is")):
            return True
    return False


def is_passive(text: str, m: re.Match) -> bool:
    """Is this be + word match a passive verb (an action), not a state?"""
    pp = m.group("pp").lower()
    rest = (m.group("rest") or "").strip().lower()
    be = m.group("be").lower().replace("\u2019", "'")
    if not ((pp.endswith("ed") and len(pp) >= 4 and pp not in NOT_PARTICIPLE) or pp in IRREGULAR_PP):
        return False
    if pp in PASSIVE_ALLOW or f"{pp} {rest}" in PASSIVE_ALLOW or pp.startswith("un"):
        return False  # adjective use: "it's fixed", "the guide is untouched"
    if be.endswith("'s") and rest in OBJECTISH:
        return False  # "she's sent the email" = she has sent: active
    if PRESENT_BE.match(be):
        if pp in STATE_PRESENT:
            return False
        adverbs = set(m.group("adv").lower().split())
        before = re.findall(r"[a-z']+", text[:m.start()].lower())[-3:]
        return bool(rest in ACTION_AFTER or adverbs & ACTION_ADVERB
                    or set(before) & ACTION_BEFORE)
    return True


def ste_issues(sentence: str, step: bool, compact: bool = False) -> list[dict]:
    """The scored STE issues for one sentence.

    compact: a client guide step. In two blind comparisons the compact guide read
    better than the strict one. A two-action fix may share a
    line ("Close it in Excel, then click Try again."); three actions still fail.
    Checklists (surface "steps") stay strict: one instruction per sentence.
    """
    issues = []
    n = ste_words(sentence)
    cap = STEPS_MAX if step else PROSE_MAX
    if n > cap:
        issues.append({"code": "LONG", "sentence": sentence,
                       "detail": f"{n} words (max {cap} for {'a step' if step else 'prose'})"})
    scrubbed = _scrub(sentence)
    for m in _PASSIVE_RX.finditer(scrubbed):
        if is_passive(scrubbed, m):
            issues.append({"code": "PASSIVE", "sentence": sentence,
                           "detail": f"{m.group('be')} {m.group('adv')}{m.group('pp')}"})
            break
    if ";" in re.sub(r"&\w+;|&#\d+;", "", scrubbed):
        issues.append({"code": "SEMICOLON", "sentence": sentence, "detail": "split it into two sentences"})
    if step:
        words = _words(scrubbed)
        low = [w.lower() for w in words]
        extra = []  # each further instruction joined by "and" / "then"
        for k, w in enumerate(low):
            j = None
            if w == "and" and k > 0:
                j = k + 1
                if j < len(low) and low[j] == "then":
                    j += 1
            elif w == "then" and k > 0:
                j = k + 1
            if j is None or j >= len(words):
                continue
            verb = imperative_at(words, j)
            if verb:
                extra.append(f"{w} {verb}")
        if len(extra) > (1 if compact else 0):
            issues.append({"code": "MULTI", "sentence": sentence,
                           "detail": f"{'third' if compact else 'second'} instruction: \"{extra[-1] if compact else extra[0]}\""})
        if opens_imperative(sentence, condition_first=False):
            for k, w in enumerate(low):
                if k < 2 or w not in SUBORDINATORS:
                    continue
                if w == "until" and {"wait", "keep", "again", "repeat"} & set(low[:k]):
                    continue  # "click it again until ...": the until is the loop's end
                if w == "if" and set(low[max(0, k - 3):k]) & WHETHER_VERBS:
                    continue  # "ask her if she enters it": if = whether
                if w == "once" and (k + 1 >= len(low) or low[k + 1] in (",", ".", "and", "more")):
                    continue
                if _has_clause(words[k + 1:]):
                    issues.append({"code": "CONDITION", "sentence": sentence,
                                   "detail": f"condition after the command (\"{w} ...\"): put it first"})
                    break
    return issues


def soft_notes(sentence: str) -> list[dict]:
    notes = []
    s = plain(sentence)
    m = ING_RX.search(s)
    before = re.findall(r"[a-z']+", s[:m.start()].lower())[-3:] if m else []
    if m and not ({"and", "or", "later"} & set(before)) and not re.match(r"^\W*\w+ing\b", s):
        notes.append({"code": "ING", "msg": f"trailing \", {m.group(1)} ...\" clause (STE 3.5). Make it its own sentence",
                      "excerpt": sentence})
    if THISVERB_RX.search(s):
        notes.append({"code": "THISVERB", "msg": "\"This\" + verb (STE GR-4). Say what \"this\" is",
                      "excerpt": sentence})
    m = LATIN_RX.search(s)
    if m:
        notes.append({"code": "LATIN", "msg": f"{m.group(1)!r} (STE GR-6). Use \"for example\", \"that is\", or list the rest",
                      "excerpt": sentence})
    return notes


def is_salutation(sentence: str) -> bool:
    s = plain(sentence)
    return bool(GREETING_RX.match(s) or SALUTATION_RX.match(s))


# ==========================================================================
# Chat-only checks

def _excerpt(text: str, start: int, end: int, pad: int = 40) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    return ("..." if a else "") + text[a:b].replace("\n", " ").strip() + ("..." if b < len(text) else "")


def _blank(text: str, rx: re.Pattern) -> str:
    return rx.sub(lambda m: " " * len(m.group(0)), text)


def chat_text_for_scan(blocks: list[Block]) -> str:
    """What the writer says in its own voice: not shell fences (already a
    COMMAND fail), not blockquotes or drafts (copy for someone else, which may
    rightly carry a path, e.g. a bug report for a developer), and not quoted
    error text, which is where the PATH false alarms came from."""
    parts = []
    for b in blocks:
        if (b.kind == "code" and b.lang in COMMAND_LANGS) or b.quote or b.draft:
            continue
        parts.append(b.text)
    text = "\n".join(parts)
    return re.sub(r"\"[^\"\n]*\"|\u201c[^\u201d\n]*\u201d", lambda m: " " * len(m.group(0)), text)


def chat_fails(raw: str, blocks: list[Block]) -> tuple[list[dict], list[str]]:
    fails: list[dict] = []
    verify: list[str] = []
    scan = chat_text_for_scan(blocks)

    # PATH: markdown links first (their targets), then bare paths with URLs blanked.
    for m in MD_LINK_RX.finditer(scan):
        if not SAFE_SCHEMES.match(m.group(2)):
            fails.append({"code": "PATH", "msg": "link to a local file. Say what it is, or give a web link",
                          "excerpt": m.group(0)})
    noweb = _blank(_blank(_blank(scan, MD_LINK_RX), URL_RX), DOMAIN_RX)
    seen = set()
    for rx in (ABS_PATH_RX, REL_PATH_RX):
        for m in rx.finditer(noweb):
            hit = m.group(0).rstrip(".,:;")
            if hit in seen or not re.search(r"[A-Za-z]", hit):
                continue
            seen.add(hit)
            fails.append({"code": "PATH", "msg": "file path in a reply. Say what the file is in plain words",
                          "excerpt": hit})
        noweb = _blank(noweb, rx)

    # HASH
    for rx, what in ((UUID_RX, "a UUID"), (HEX_RX, "a commit hash"), (TOOLID_RX, "a tool or agent id")):
        for m in rx.finditer(noweb):
            hit = m.group(0)
            if hit in seen:
                continue
            seen.add(hit)
            fails.append({"code": "HASH", "msg": f"{what} in a reply. Say what it points to instead",
                          "excerpt": hit})
        noweb = _blank(noweb, rx)

    # COMMAND: a shell fence, or a "$ " line anywhere outside one. Read from
    # the raw lines, because parse() joins a paragraph's lines into one.
    for b in blocks:
        if b.kind == "code" and b.lang in COMMAND_LANGS:
            first = next((ln for ln in b.text.splitlines() if ln.strip()), "")
            fails.append({"code": "COMMAND", "msg": f"a {b.lang} block. The reader does not run commands; run it or describe it",
                          "excerpt": first.strip()[:80]})
    fence = None
    for line in raw.splitlines():
        m = FENCE_RX.match(line)
        if m:
            fence = None if fence else m.group(2).lower()
            continue
        if fence in COMMAND_LANGS:
            continue
        if DOLLAR_CMD_RX.match(re.sub(r"^\s*>\s?", "", line)):
            fails.append({"code": "COMMAND", "msg": "a shell line. The reader does not run commands",
                          "excerpt": line.strip()[:80]})

    # OPENER: the first words of the reply itself.
    m = OPENER_RX.match(raw.lstrip())
    if m:
        fails.append({"code": "OPENER", "msg": f"opens with {m.group(1)!r}. Lead with the answer",
                      "excerpt": raw.strip().splitlines()[0][:80]})

    # BANNED: copy_rules FILLER and BANNED, bare ramp, in the writer's own words:
    # not code, drafts or blockquotes, and not quoted mentions. The false alarms
    # here were quoted copy under discussion ('The close is now just "I'd love
    # to talk!"').
    prose = "\n".join(b.text for b in blocks if b.kind != "code" and not b.draft and not b.quote)
    prose = re.sub(r"`[^`]*`", " ", prose)
    prose = re.sub(r"(?<!\w)[\"\u201c][^\"\u201d\n]{1,300}[\"\u201d](?!\w)", lambda mm: " " * len(mm.group(0)), prose)
    prose = re.sub(r"(?<!\w)['\u2018]([^'\u2019\n]{1,40})['\u2019](?!\w)",
                   lambda mm: " " * len(mm.group(0)) if len(mm.group(1).split()) <= 4 else mm.group(0), prose)
    hard, _ = copy_rules.shared_checks(prose, "artifact")
    for line in hard:
        code = line.split()[0]
        if code == "FILLER" or (code == "BANNED" and "quietly" not in line):
            fails.append({"code": "BANNED", "msg": line.split(None, 1)[1], "excerpt": _word_excerpt(prose, line)})
    for m in RAMP_RX.finditer(prose):
        fails.append({"code": "BANNED", "msg": "contains 'ramp'. Say training, learning the job, up to speed",
                      "excerpt": _excerpt(prose, m.start(), m.end())})

    # DRAFT: outgoing copy inside the reply gets the email-level shared checks.
    for b in blocks:
        if not b.draft:
            continue
        hard, ver = copy_rules.shared_checks(b.text, "email")
        for line in hard:
            code = line.split()[0]
            if code in ("DASH", "FILLER", "BANNED"):
                fails.append({"code": "DRAFT", "msg": "in a draft: " + line.split(None, 1)[1],
                              "excerpt": _word_excerpt(b.text, line)})
            else:
                verify.append("DRAFT     " + line)
        verify.extend("DRAFT     " + v for v in ver)
    return fails, verify


def _word_excerpt(text: str, line: str) -> str:
    """Find the thing a copy_rules line names in the text, for the excerpt."""
    m = re.search(r"contains (?:a |an )?(?:'([^']+)'|\"([^\"]+)\")|contains a (em|en) dash", line)
    if not m:
        return ""
    if m.group(3):
        ch = "\u2014" if m.group(3) == "em" else "\u2013"
        k = text.find(ch)
    else:
        mm = re.search(r"\b" + re.escape(m.group(1) or m.group(2)), text, re.I)
        k = mm.start() if mm else -1
    return _excerpt(text, k, k + 1) if k >= 0 else ""


def mark_drafts(blocks: list[Block]) -> None:
    """Find outgoing copy inside a chat reply.

    A fenced block with no language (or text/email/markdown/md) holding 15+
    words of prose. Also a blockquote run, or a section between two "---"
    rules, that opens with a greeting."""
    for b in blocks:
        if b.kind == "code" and b.lang in DRAFT_LANGS:
            toks = b.text.split()
            prose_words = [t for t in toks if re.fullmatch(r"[A-Za-z][A-Za-z'\u2019-]*[.,!?:]?", t)]
            if len(prose_words) >= 15 and len(prose_words) >= 0.6 * len(toks):
                b.draft = True
    # Blockquote runs with a greeting in their first lines ("> **Subject:** ...",
    # "> Hey Dana,"). A quote without one is someone else's words, not a draft.
    k = 0
    while k < len(blocks):
        if blocks[k].quote and blocks[k].kind != "code":
            run = [blocks[k]]
            while k + 1 < len(blocks) and blocks[k + 1].quote:
                k += 1
                run.append(blocks[k])
            if any(GREETING_START_RX.match(plain(r.text)) for r in run[:3]):
                for r in run:
                    r.draft = True
        k += 1
    # "---" sections opening with a greeting. With one rule, the draft runs to
    # the end; with more, only sections closed by a rule count.
    hrs = [i for i, b in enumerate(blocks) if b.kind == "hr"]
    bounds = list(zip(hrs, hrs[1:])) + ([(hrs[0], len(blocks))] if len(hrs) == 1 else [])
    for a, z in bounds:
        sec = blocks[a + 1:z]
        if sec and sec[0].kind == "para" and GREETING_START_RX.match(plain(sec[0].text)):
            for r in sec:
                r.draft = True


# ==========================================================================
# The run

def classify_steps(blocks: list[Block], surface: str) -> None:
    for b in blocks:
        if surface == "steps":
            b.step = b.kind not in ("code", "table", "heading", "hr")
        elif b.kind == "check":
            b.step = True
        elif b.kind == "number":
            b.step = True if surface != "chat" else opens_imperative(first_sentence(b.text))
        elif b.kind == "bullet":
            b.step = opens_imperative(first_sentence(b.text))


def first_sentence(text: str) -> str:
    s = split_sentences(text)
    return s[0] if s else text


def _blank_to_steps(text: str) -> str:
    """The steps surface is one step per line: make each line its own item."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if not (CHECK_RX.match(line) or NUMBER_RX.match(line) or BULLET_RX.match(line)
                or HEADING_RX.match(line) or FENCE_RX.match(line)):
            line = "- " + line.strip()
        out.append(line)
    return "\n".join(out)


_OPEN_Q = "\"\u201c'\u2018"
_CLOSE_Q = "\"\u201d'\u2019"


def _quoted(sentence: str) -> bool:
    """Is this sentence someone else's words: it opens or closes on a quote
    mark ("Do you ever write off claims?" as a candidate email line)."""
    s = plain(sentence).strip("*_ ")
    return bool(s) and (s[0] in _OPEN_Q or s[-1] in _CLOSE_Q)


_PREPARED_RX = re.compile(r"\b(ask|asks|asked|question|questions|answer|answers)\b")


def asked_questions(blocks: list[Block]) -> list[str]:
    """Questions the writer asks the reader. Not quoted lines, not drafts or
    blockquotes, and not a prepared list of 3+ question items (questions for a
    call with someone else). Counting every "?" sentence flagged quoted and
    prepared lists about half the time."""
    listed = [False] * len(blocks)
    k = 0
    while k < len(blocks):
        j = k
        while (j < len(blocks) and blocks[j].kind in ("bullet", "number", "check")
               and plain(blocks[j].text).rstrip("*_ ").endswith("?")):
            j += 1
        if j - k >= 3:
            for i in range(k, j):
                listed[i] = True
        k = max(j, k + 1)
    asked: list[str] = []
    heading = ""
    for b, skip in zip(blocks, listed):
        if b.kind == "heading":
            heading = b.text.lower()
            continue
        if b.kind == "para":
            heading = ""
        if skip or b.draft or b.quote or b.kind in ("code", "table", "hr"):
            continue
        if b.kind != "para" and _PREPARED_RX.search(heading):
            continue  # a list under "**Ask her while she's there:**"
        lead = LEAD_LABEL_RX.match(b.text)
        if lead and _PREPARED_RX.search(lead.group(1).lower()):
            continue  # "**Ask the vendor three questions:** Is your ...? Does ...?"
        for sent in split_sentences(MD_LINK_RX.sub("link", b.text)):
            p = plain(sent).rstrip("*_ ")
            if not p.endswith("?") or _quoted(sent):
                continue
            if sent.lstrip().startswith(("**", "__")) and (sent.count("**") + sent.count("__")) % 2:
                continue  # inside a bold title: "**Are you ready? Yes.**"
            if re.fullmatch(r"\s*(\*\*|__)[^*_]+\1\s*", sent):
                continue  # a bold title: "**Is Dana an admin?**"
            if ":" in sent and _PREPARED_RX.search(sent.split(":", 1)[0].lower()):
                continue  # "Get one answer from Priya: does Dana ...?"
            asked.append(plain(sent))
    return asked


def chat_notes(text: str, blocks: list[Block], words_total: int, headers: int,
               questions: list[str]) -> list[dict]:
    """FORMAT and QUESTIONS: the shape of a claude.ai reply. Soft, never fails."""
    notes = []
    if headers and words_total < 300:
        first = next(l for l in text.splitlines() if HEADING_RX.match(l)).strip()
        notes.append({"code": "FORMAT", "msg": f"a header in a {words_total}-word reply. Short replies are plain prose",
                      "excerpt": first})
    lines = [l for l in text.splitlines() if l.strip()]
    items = [b for b in blocks if b.kind in ("bullet", "number", "check")]
    if lines and items and words_total < 150 and len(items) / len(lines) > 0.6:
        # Lists that are fine in chat (genuinely parallel items): a list of
        # steps, a list of short data rows (bills, files), or a list under at
        # least two sentences of prose. Without these exceptions the bare 60%
        # rule fired only on lists like these.
        steps_only = all(b.step for b in items)
        short_rows = sum(len(b.text.split()) for b in items) / len(items) <= 6
        lead = sum(len(split_sentences(b.text)) for b in blocks
                   if b.kind == "para" and not b.draft and not b.quote)
        if not (steps_only or short_rows or lead >= 2):
            notes.append({"code": "FORMAT", "msg": f"{len(items)} of {len(lines)} lines are bullets in a short reply. Lead with a sentence",
                          "excerpt": _short(items[0].text)})
    if len(questions) > 1:
        notes.append({"code": "QUESTIONS", "msg": f"{len(questions)} questions. Ask the one that matters",
                      "excerpt": " / ".join(questions[:3])})
    return notes


def run(text: str, surface: str, html: bool = False, proof: list[str] | None = None) -> dict:
    """Check one text for one surface. Returns the JSON shape in docs/CONTRACT.md."""
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r}")
    if html:
        text = html_to_text(text)
    if surface == "steps":
        text = _blank_to_steps(text)
    blocks = parse(text)
    classify_steps(blocks, surface)
    if surface == "chat":
        mark_drafts(blocks)

    fails: list[dict] = []
    notes: list[dict] = []
    verify: list[str] = []

    # Surface gates borrowed from copy_rules.
    if surface in PROFILE:
        body = "\n".join(b.text for b in blocks if b.kind != "code") if html else text
        hard, ver = copy_rules.check(body, PROFILE[surface], proof)
        for line in hard:
            code, msg = (line.split(None, 1) + [""])[:2]
            fails.append({"code": code, "msg": msg, "excerpt": _word_excerpt(body, line)})
        verify.extend(ver)
    elif surface == "chat":
        f, v = chat_fails(text, blocks)
        fails.extend(f)
        verify.extend(v)

    # STE, sentence by sentence. Code, tables, headings, greetings and
    # sign-offs are never scored.
    prose = {"total": 0, "pass": 0, "issues": []}
    steps = {"total": 0, "pass": 0, "issues": []}
    n_sent = 0
    strict = surface in ("guide", "steps")
    for b in blocks:
        if b.kind in ("code", "table", "heading", "hr"):
            continue
        if surface == "chat" and b.quote and not b.draft:
            continue  # someone else's words (a transcript line, an email)
        para_sents = 0
        for k, sent in enumerate(split_sentences(b.text)):
            if not re.search(r"[a-z]", sent) or is_salutation(sent):
                continue  # no lowercase: a label ("STEP 02"), not a sentence
            if len(_words(_scrub(sent))) < 2 and not b.step:
                continue  # a fragment: "Done.", "Sam"
            is_step = b.step and (k == 0 or opens_imperative(sent))
            issues = ste_issues(sent, is_step, compact=surface == "guide")
            bucket = steps if is_step else prose
            bucket["total"] += 1
            bucket["pass"] += not issues
            bucket["issues"].extend(issues)
            n_sent += 1
            para_sents += 1
            notes.extend(soft_notes(sent))
            if is_step and strict:
                fails.extend({"code": i["code"], "msg": f"step breaks STE: {i['detail']}",
                              "excerpt": i["sentence"]} for i in issues)
        if b.kind == "para" and para_sents > PARA_MAX and not _quoted(b.text):
            item = {"code": "PARA6", "msg": f"paragraph of {para_sents} sentences (STE 6.6 max {PARA_MAX}). Split it",
                    "excerpt": _short(b.text)}
            (fails if surface == "guide" else notes).append(item)

    words_total = copy_rules.words(text)
    headers = sum(1 for line in text.splitlines() if HEADING_RX.match(line))
    questions = asked_questions(blocks)
    if surface == "chat":
        notes.extend(chat_notes(text, blocks, words_total, headers, questions))

    def score(d, target):
        sc = round(d["pass"] / d["total"], 3) if d["total"] else 1.0
        return {"total": d["total"], "pass": d["pass"], "score": sc, "target": target, "issues": d["issues"]}

    return {
        "surface": surface,
        "ok": not fails,
        "fails": fails,
        "notes": notes,
        "verify": verify,
        "ste": {"prose": score(prose, PROSE_TARGET), "steps": score(steps, STEPS_TARGET)},
        "stats": {"words": words_total, "sentences": n_sent,
                  "bullets": sum(1 for b in blocks if b.kind in ("bullet", "number", "check")),
                  "headers": headers, "questions": len(questions)},
    }


def _short(s: str, n: int = 90) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n - 3] + "..."


def render(res: dict, html: bool) -> str:
    p, s = res["ste"]["prose"], res["ste"]["steps"]
    src = " (html)" if html else ""
    out = [f"voice_check \u00b7 {res['surface']}{src} \u00b7 {res['stats']['words']} words \u00b7 "
           f"prose {p['score']:.2f} ({p['pass']}/{p['total']}, target {p['target']:.2f}) \u00b7 "
           f"steps {s['score']:.2f} ({s['pass']}/{s['total']}, target {s['target']:.2f})"]
    for f in res["fails"]:
        out.append(f"  FAIL   {f['code']:<9} {f['msg']}" + (f": {_short(f['excerpt'])!r}" if f["excerpt"] else ""))
    for n in res["notes"]:
        out.append(f"  NOTE   {n['code']:<9} {n['msg']}" + (f": {_short(n['excerpt'])!r}" if n["excerpt"] else ""))
    strict = res["surface"] in ("guide", "steps")
    for kind, d in (("step", s), ("prose", p)):
        if kind == "step" and strict:
            continue  # already printed as FAIL lines
        for iss in d["issues"]:
            out.append(f"  STE    {kind:<5} {iss['code']:<9} {iss['detail']}: {_short(iss['sentence'])!r}")
    for v in res["verify"]:
        out.append("  VERIFY " + v)
    if res["ok"] and not res["notes"] and not p["issues"] and not s["issues"]:
        out.append("  clean.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check a reply or draft against the house rules and STE.")
    ap.add_argument("file", help="path to the text, or - for stdin")
    ap.add_argument("--surface", required=True, choices=SURFACES)
    ap.add_argument("--html", action="store_true", help="strip HTML first, keeping <ol><li> and checkbox steps")
    ap.add_argument("--json", action="store_true", help="print the JSON report")
    ap.add_argument("--proof", action="append", default=[], help="a proof file for the claim check (repeatable)")
    a = ap.parse_args()
    raw = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
    html = a.html or (a.file != "-" and Path(a.file).suffix.lower() in (".html", ".htm"))
    res = run(raw, a.surface, html, a.proof)
    print(json.dumps(res, ensure_ascii=False) if a.json else render(res, html))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
