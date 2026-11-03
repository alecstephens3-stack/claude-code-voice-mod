#!/usr/bin/env python3
"""
build_ab_page.py: turn an eval run into a blind side-by-side page.

Reads data/eval/<run>/results.json and summary.json (the newest run unless --run
names one), shuffles which reply is A and which is B per question (seeded by the
run name, so a rebuild keeps the same order), fills eval/ab-template.html, copies
the design-system blocks out of pages/checklist-template.html, and writes
data/eval/<run>/blind-test.html. Picks save through the page server's checklist
API under the id voice-blind-test-<run>, or in the browser when it is offline.

    python3 eval/build_ab_page.py
    python3 eval/build_ab_page.py --run 2026-10-03-2140
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD = HERE.parent
RUNS = MOD / "data/eval"
CHECKLIST = MOD / "pages/checklist-template.html"
KINDS = {"status": "Status", "steps": "Steps", "explain": "Explain", "email": "Email draft", "judgment": "Judgment call"}
MARKERS = ("FONTS", "KIT", "LOGO", "JS")


def pct(x) -> str:
    return "n/a" if x is None else f"{round(x * 100)}%"


def kit_blocks() -> dict[str, str]:
    """The inlined design-system blocks of the checklist page, by marker name."""
    src = CHECKLIST.read_text(encoding="utf-8")
    blocks = {}
    for name in MARKERS:
        m = re.search(rf"<!-- SAI:{name} -->.*?<!-- /SAI:{name} -->", src, re.S)
        if not m:
            raise SystemExit(f"{CHECKLIST.name} has no SAI:{name} block")
        blocks[name] = m.group(0)
    return blocks


def score_rows(b: dict, m: dict) -> list[dict]:
    slips = lambda s: sum(s["fails"].values())
    paths = lambda s: s["fails"].get("PATH", 0) + s["fails"].get("HASH", 0)
    return [
        {"label": "Replies with a hard slip (paths, IDs, commands, banned words)", "base": f'{b["replies_with_any_fail"]} of {b["replies"]}', "mod": f'{m["replies_with_any_fail"]} of {m["replies"]}'},
        {"label": "Hard slips in total", "base": str(slips(b)), "mod": str(slips(m))},
        {"label": "File paths and IDs shown to the reader", "base": str(paths(b)), "mod": str(paths(m))},
        {"label": "Sentences passing the light STE check", "base": pct(b["prose_ste"]), "mod": pct(m["prose_ste"])},
        {"label": "Steps passing strict STE", "base": f'{pct(b["steps_ste"])} of {b["steps_total"]}', "mod": f'{pct(m["steps_ste"])} of {m["steps_total"]}'},
        {"label": "Headers", "base": str(b["headers"]), "mod": str(m["headers"])},
        {"label": "Bullet lines", "base": str(b["bullets"]), "mod": str(m["bullets"])},
        {"label": "Median reply length, words", "base": str(b["median_words"]), "mod": str(m["median_words"])},
        {"label": "Checklists opened as pages", "base": str(b["checklist_pages"]), "mod": str(m["checklist_pages"])},
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="eval run folder name; newest by default")
    args = ap.parse_args()
    runs = sorted(p for p in RUNS.glob("*") if (p / "summary.json").exists()) if RUNS.exists() else []
    if not args.run and not runs:
        print("No finished eval run found. Run eval/run_eval.py first.", file=sys.stderr)
        return 1
    run = (RUNS / args.run) if args.run else runs[-1]
    rows = json.loads((run / "results.json").read_text())
    summary = json.loads((run / "summary.json").read_text())
    if len(rows) < 2:
        print("This run has fewer than 2 questions; run the full eval first.", file=sys.stderr)
        return 1

    rng = random.Random(run.name)
    questions = [{
        "n": r["n"], "prompt": r["prompt"], "kind": KINDS.get(r["kind"], r["kind"]),
        "modIsA": rng.random() < 0.5,
        "base": {"reply": r["base"]["reply"], "checklists": r["base"].get("checklists", [])},
        "mod": {"reply": r["mod"]["reply"], "checklists": r["mod"].get("checklists", [])},
        # A reply re-run by hand carries a "note" in results.json. The wording must
        # not reveal which side was re-run.
        "note": "One of these two replies comes from a second, identical run, because the first try ran out of turns before it answered."
        if (r["base"].get("note") or r["mod"].get("note")) else "",
    } for r in rows]

    try:
        human = date.fromisoformat(run.name[:10]).strftime("%-d %b %Y").upper()
    except ValueError:
        human = run.name
    data = {"id": f"voice-blind-test-{run.name}", "questions": questions,
            "scoreRows": score_rows(summary["base"], summary["mod"])}

    html = (HERE / "ab-template.html").read_text(encoding="utf-8")
    # </script> inside the JSON would end the script tag early.
    html = html.replace("/*DATA*/", json.dumps(data).replace("</", "<\\/"))
    html = html.replace("__DATE__", human)
    for name, block in kit_blocks().items():
        html = html.replace(f"<!-- SAI:{name} -->", block)
    out = run / "blind-test.html"
    out.write_text(html, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
