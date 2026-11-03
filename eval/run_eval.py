#!/usr/bin/env python3
"""
run_eval.py: the before/after test for the voice mod.

Asks Claude Code the same questions twice, headless, in your project: once without
the mod and once with the mod loaded from this folder. Then scores every reply with
checker/voice_check.py (chat surface) and writes a summary plus the raw replies, which
eval/build_ab_page.py turns into a blind side-by-side page.

Nothing can leave the machine: permission mode is forced to default (so any tool that
needs approval is refused in a headless run), every write and send tool is disallowed
by name, and VOICE_MOD_EVAL=1 keeps the mod from opening a browser or calling the page
server.

The project needs .claude/voice-mod.json, or the mod stands aside in the "mod" arm too.
If you also have the mod installed from a marketplace, set VOICE_MOD_INSTALLED_ID (for
example voice-mod@my-marketplace) so the installed copy is switched off in both arms.

    python3 eval/run_eval.py --project ~/work/my-project             # all prompts, both arms
    python3 eval/run_eval.py --project ~/work/my-project --only 3    # one prompt
    CLAUDE_BIN=/path/to/claude python3 eval/run_eval.py --project .
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD = HERE.parent
CHECKER = MOD / "checker/voice_check.py"
RUNS = MOD / "data/eval"

DISALLOWED = [
    "Write", "Edit", "NotebookEdit",
    "mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Gmail__create_draft", "mcp__claude_ai_Gmail__reply",
    "mcp__claude_ai_Gmail__forward", "mcp__claude_ai_Gmail__update_draft",
    "mcp__claude_ai_Slack__slack_send_message", "mcp__claude_ai_Slack__slack_schedule_message",
    "mcp__claude_ai_Google_Calendar__create_event", "mcp__claude_ai_Google_Calendar__update_event",
    "mcp__claude_ai_Microsoft_365__outlook_send_mail", "mcp__claude_ai_Microsoft_365__outlook_create_draft",
]
ALLOWED = ["Read", "Grep", "Glob",
           "mcp__voice-mod__open_checklist", "mcp__voice-mod__open_page", "mcp__voice-mod__read_checklist"]


def claude_bin() -> str:
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not found:
        raise SystemExit("Claude Code not found. Put `claude` on PATH or set CLAUDE_BIN.")
    return found


def ask(prompt: str, arm: str, project: Path) -> dict:
    argv = [claude_bin(), "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--setting-sources", "user,project,local", "--permission-mode", "default",
            "--max-turns", "30",
            "--allowedTools", *ALLOWED, "--disallowedTools", *DISALLOWED]
    installed = os.environ.get("VOICE_MOD_INSTALLED_ID")
    if installed:
        argv += ["--settings", json.dumps({"enabledPlugins": {installed: False}})]
    if arm == "mod":
        argv += ["--plugin-dir", str(MOD)]
    env = {**os.environ, "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1", "VOICE_MOD_EVAL": "1"}
    p = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    result, tools, pages, pending = "", [], [], {}
    for line in p.stdout.splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("type") == "assistant":
            for b in o["message"].get("content", []):
                if b.get("type") == "tool_use":
                    tools.append(b["name"])
                    if b["name"] == "mcp__voice-mod__open_checklist":
                        pending[b["id"]] = b.get("input", {})
        if o.get("type") == "user":
            content = o.get("message", {}).get("content", [])
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    page = pending.pop(b["tool_use_id"])
                    if not b.get("is_error"):  # a refused checklist never reached the reader
                        pages.append(page)
        if o.get("type") == "result":
            result = o.get("result") or ""
    return {"reply": result, "tools": tools, "checklists": pages, "exit": p.returncode,
            "stderr": p.stderr[-400:] if p.returncode else ""}


def check(text: str, surface: str) -> dict:
    p = subprocess.run([sys.executable, str(CHECKER), "-", "--surface", surface, "--json"],
                       input=text, capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"error": p.stderr[-300:]}


def summarize(rows: list[dict], name: str) -> dict:
    def agg(arm: str) -> dict:
        checks = [r[arm]["check"] for r in rows if r[arm].get("check", {}).get("ste")]
        fails: dict[str, int] = {}
        for c in checks:
            for f in c["fails"]:
                fails[f["code"]] = fails.get(f["code"], 0) + 1
        pt = sum(c["ste"]["prose"]["total"] for c in checks)
        pp = sum(c["ste"]["prose"]["pass"] for c in checks)
        st = sum(c["ste"]["steps"]["total"] for c in checks)
        sp = sum(c["ste"]["steps"]["pass"] for c in checks)
        for r in rows:  # steps that went to a checklist page count too
            for page in r[arm].get("checklists", []):
                texts = [s.get("text", "") if isinstance(s, dict) else str(s) for s in page.get("steps", [])]
                j = check("\n".join(texts), "steps")
                if "ste" in j:
                    st += j["ste"]["steps"]["total"]
                    sp += j["ste"]["steps"]["pass"]
        words = [c["stats"]["words"] for c in checks]
        return {
            "replies": len(checks),
            "fails": fails,
            "replies_with_any_fail": sum(1 for c in checks if c["fails"]),
            "prose_ste": round(pp / pt, 3) if pt else None,
            "steps_ste": round(sp / st, 3) if st else None,
            "steps_total": st,
            "headers": sum(c["stats"]["headers"] for c in checks),
            "bullets": sum(c["stats"]["bullets"] for c in checks),
            "median_words": sorted(words)[len(words) // 2] if words else 0,
            "checklist_pages": sum(len(r[arm].get("checklists", [])) for r in rows),
        }

    return {"run": name, "base": agg("base"), "mod": agg("mod")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".", help="the project to ask the questions in")
    ap.add_argument("--prompts", default=str(HERE / "prompts.json"))
    ap.add_argument("--only", help="1-based prompt numbers, comma separated")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--arms", default="base,mod", help="which arms to run, comma separated")
    ap.add_argument("--rescore", help="recompute summary.json for an existing run folder name")
    args = ap.parse_args()
    if args.rescore:
        run_dir = RUNS / args.rescore
        rows = json.loads((run_dir / "results.json").read_text())
        summary = summarize(rows, run_dir.name)
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps(summary, indent=1))
        return 0

    project = Path(args.project).expanduser().resolve()
    prompts = json.loads(Path(args.prompts).read_text())
    numbers = list(range(1, len(prompts) + 1))
    if args.only:
        numbers = [int(x) for x in args.only.split(",")]
        prompts = [prompts[n - 1] for n in numbers]
    run_dir = RUNS / datetime.now().strftime("%Y-%m-%d-%H%M")
    run_dir.mkdir(parents=True, exist_ok=True)

    arms = args.arms.split(",")
    jobs = [(n, p, arm) for n, p in zip(numbers, prompts) for arm in arms]
    results: dict[tuple[int, str], dict] = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(ask, p["prompt"], arm, project): (i, arm) for i, p, arm in jobs}
        for f in cf.as_completed(futs):
            i, arm = futs[f]
            r = f.result()
            r["check"] = check(r["reply"], "chat") if r["reply"] else {}
            results[(i, arm)] = r
            print(f"  prompt {i} {arm}: {len(r['reply'].split())} words, tools {r['tools'] or '-'}", flush=True)

    rows = []
    for i, p in zip(numbers, prompts):
        row = {"n": i, "prompt": p["prompt"], "kind": p["kind"]}
        for arm in arms:
            row[arm] = results[(i, arm)]
        rows.append(row)
    (run_dir / "results.json").write_text(json.dumps(rows, indent=1))

    if set(arms) != {"base", "mod"}:
        print(f"Saved one arm to {run_dir}; merge it with a run that has the other, then --rescore.")
        return 0
    summary = summarize(rows, run_dir.name)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    print(f"\nSaved to {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
