# Interfaces

I built the checker, the page server and the mod as three separate pieces and fixed the
interfaces between them first, so each could be tested on its own. This file is that
contract, kept current.

## The checker

    python3 checker/voice_check.py <file|-> --surface <surface> [--html] [--json] [--proof FILE ...]

`--html` (or a file ending in `.html`) strips tags first but keeps the step structure:
`<ol><li>` items, checkbox items and `class="step"` elements count as steps.

| surface | what it is | fails | STE |
|---|---|---|---|
| chat | a reply in the chat panel | PATH, HASH, COMMAND, OPENER, BANNED, DRAFT | reported, never fails |
| email | a client email body | copy_rules `email` profile | reported, never fails |
| guide | a how-to guide or manual | copy_rules `artifact` profile, every step that breaks strict STE (compact: one paired action allowed), PARA6 | steps gate |
| artifact | any other HTML page | copy_rules `artifact` profile | reported |
| social | social or website copy | copy_rules `artifact` profile | reported |
| steps | one checklist step per line | every step that breaks strict STE | steps gate |

`--json` prints exactly these keys:

    {"surface": "chat", "ok": true,
     "fails":  [{"code": "PATH", "msg": "...", "excerpt": "..."}],
     "notes":  [{"code": "FORMAT", "msg": "...", "excerpt": "..."}],
     "verify": ["..."],
     "ste": {"prose": {"total": 0, "pass": 0, "score": 1.0, "target": 0.8,
                       "issues": [{"code": "LONG", "sentence": "...", "detail": "31 words"}]},
             "steps": {"total": 0, "pass": 0, "score": 1.0, "target": 1.0, "issues": []}},
     "stats": {"words": 0, "sentences": 0, "bullets": 0, "headers": 0, "questions": 0}}

A score with a total of 0 is 1.0. `ok` is false when there is any fail. The exit code is
0 when ok and 1 otherwise. Prose passes when it is not LONG, not PASSIVE and has no
SEMICOLON; a step must also not be MULTI or CONDITION.

## The page server

`server/checklist_server.py`, on 127.0.0.1 only. Requests with a Host other than
`localhost:<port>` or `127.0.0.1:<port>` get 403, and so does a POST from another Origin.

- `GET /api/health` returns `{"ok": true}`.
- `GET /pages/<rel>` serves `<root>/<rel>` when it ends in `.html`, resolves inside the
  root, and sits under `artifacts/` or `clients/<name>/(artifacts|deliverables)/`.
  Anything else is 404.
- `GET /api/checklist/<id>`, where `id` matches `^[a-z0-9][a-z0-9-]{0,80}$`, returns the
  saved record or `{"id": id, "ticks": {}, "updated": null}`.
- `POST /api/checklist/<id>` takes `{"ticks": {"s1": true}}` plus optional `title` and
  `items` (`[{"id", "text"}]`), merges the ticks, sets `updated`, writes the file
  atomically and returns the record. Bodies over 64 KB get 413.

## The checklist page

`pages/checklist-template.html` carries five placeholders that the mod fills by plain
string replacement: `{{TITLE}}` (tab title and heading), `{{INTRO}}` (may be empty),
`{{CHECKLIST_ID}}`, `{{CREATED}}` and `{{ITEMS}}`. Each step is one element:

    <li class="ck-item" data-id="s1"><label><input type="checkbox" id="s1"><span class="ck-text">Open the app.</span><span class="ck-detail">Optional detail.</span></label><div class="ck-paste">...</div></li>

The detail and the paste box are left out when the step has none. The paste box sits
outside the label so pressing Copy never ticks the step.

On load the page reads `/api/checklist/<id>`; on every change it posts the full ticks
map (with title and items on the first save). localStorage keeps a copy under
`checklist:<id>`, so the page works as a file and offline ticks are sent on the next load.

## Tools the mod registers

The model sees them as `mcp__voice-mod__<name>`.

- `open_checklist({ title, intro?, steps: [{ text, detail?, copy? }], id? })` checks the
  steps on the `steps` surface, writes the page into `pagesDir`, registers the title and
  items with the page server, and opens it. Steps that fail come back as a refusal with
  the reasons.
- `open_page({ path })` opens an existing `.html` page under the page folders, after the
  `artifact` or `guide` check (and the design fingerprint, when one is configured).
- `read_checklist({ id })` returns the ticks as `[x]` / `[ ]` lines.

## Config

`.claude/voice-mod.json` in the project. The mod does nothing in a project without it.
Every key is optional; `hooks/lib/config.ts` has the defaults and `examples/voice-mod.json`
shows them all.
