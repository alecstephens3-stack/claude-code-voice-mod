# claude-code-voice-mod

A Claude Code mod (a plugin with TypeScript function hooks) that gives the agent one
consistent writing voice, checks every reply and every outgoing draft against it, and moves
step-by-step browser tasks out of the chat into a checklist page whose ticks are saved.

I run most of my consultancy's day-to-day through Claude Code in the VS Code panel. Inside
Claude Code the model writes like a terminal tool: file paths in every answer, commands I'm
expected to run, headers on a three-sentence reply. Rules in CLAUDE.md didn't hold, so this mod
moves the voice into the system prompt and backs it with a deterministic check that catches a
slip on the turn it happens.

## How it works

Everything hangs off hooks registered in `hooks/register.ts`. The logic lives in pure
functions under `hooks/lib/`, which is what the tests cover.

`prompt.compose` puts `voice/voice.md` into the system prompt as its own section, ahead of
the session sections, and rewrites the built-in lines that describe output as markdown "in
a terminal" or ask for `file_path:line_number` references. A rule in CLAUDE.md arrives as
a user message; this mod puts it in the system prompt instead.

`turn.step` fixes two kinds of slip while the reply streams: a stock opener ("Certainly!",
"Absolutely,") is dropped, and the stiff verb forms listed in `hooks/lib/live.ts` become
"use", "used" or "using", outside code. That's all it fixes live, because both have exactly
one right answer. Text is held only up to the next word boundary, so streaming stays live.

`turn.complete` runs `checker/voice_check.py` on the finished reply. When something is off
(a file path, a commit hash, a shell command, two questions, a weak STE score) the mod
appends a user-role note that the model reads and the panel never shows, telling it to fix
the pattern from its next reply on. The same hook writes one row per reply to a score log,
with counts and codes only, never the text.

`tool.call` is the gate on outgoing copy. A `Write` or `Edit` into a client-facing folder
(`artifacts/`, `clients/<name>/deliverables/`, `content/social/` and a few more, see
`hooks/lib/gate.ts`) or a Gmail or Outlook draft or send gets checked first. A hard failure,
such as an em dash in a client email, refuses the call and hands back the reasons. A line
that has to stay word for word can carry `voice-check: keep <reason>`.

`prompt.submit` adds a hidden line with the current time where the user is and in each
client time zone, with business hours open or closed, so the model never does time zone
arithmetic in its head. Daylight saving comes from the time zone database.

There are also three tools. `open_checklist` takes steps, checks each one against strict
Simplified Technical English (one command, 20 words or fewer, condition first, active
voice), renders `pages/checklist-template.html` and opens it in the browser.
`read_checklist` reads the ticks back, so the model checks before it claims a step is done.
`open_page` opens an HTML report after checking its copy. If the model writes three or more
checkboxes into chat anyway, the mod opens them as a page and tells it to use the tool next
time.

A small guard watches for replies that say a meeting needs a new date or someone hasn't
replied. If no calendar or inbox lookup happened in that turn, the model gets a note to go
and look. I added it after the model made that claim four times in one week while the
answer sat in the inbox.

## The checker

`checker/voice_check.py` is standard-library Python and makes no model calls. It scores
text on six surfaces (`chat`, `email`, `guide`, `artifact`, `social`, `steps`) and prints
JSON that the mod reads; `docs/CONTRACT.md` has the shape. Word counting follows STE rule 8,
so a number with its unit, a hyphenated word, a quoted phrase or a run of capitalized
words counts as one word. Steps are held to 20 words and prose to 25.

A lot of the checker is about false alarms. The passive check lets "Everything is saved"
through, because a present-tense participle there describes a state. A bold button name
after "Click", as in "Click **Save and check the connection**", counts as one word, so it
can't trip the one-instruction rule. In "Ask her if she enters it" the "if" means whether,
and the condition rule skips it. Each of these has a test that keeps it from coming back.

`checker/copy_rules.py` holds the word lists, the email rules (under 100 words, about one
exclamation point, a greeting and a sign-off) and a claim check: any number with a unit
that doesn't appear in the proof files you list becomes a VERIFY line.

## The eval

`eval/run_eval.py` asks Claude Code the same ten questions twice in a real project, once
without the mod and once with it loaded from this folder, scores both sets of replies with
the checker, and writes a summary. Write and send tools are disallowed by name and
`VOICE_MOD_EVAL=1` stops the mod from opening a browser. `eval/build_ab_page.py` turns a run
into a blind A/B page: A and B are shuffled per question with a seed taken from the run
name, you pick the better reply, and a reveal button shows which side was the mod next to
the checker's numbers.

    python3 eval/run_eval.py --project ~/work/my-project
    python3 eval/build_ab_page.py

The prompts in `eval/prompts.json` are placeholders. Replace them with questions about your
own project, because a reply about files that don't exist tells you little. Each run is 20
headless Claude Code sessions, so it costs real tokens.

## Install

You need Claude Code with mods (function hooks), Python 3.9 or newer, and Node 18 or newer
for the tests. I built this against Claude Code 2.1.286 to 2.1.314; mods are early access
and the API changes between releases.

    git clone <this repo> /path/to/claude-code-voice-mod
    cd your-project
    mkdir -p .claude && echo '{}' > .claude/voice-mod.json
    claude --plugin-dir /path/to/claude-code-voice-mod

The eval runs Claude Code with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`. If your build loads the
plugin but none of the hooks fire, set that variable too.

The mod does nothing in a project without `.claude/voice-mod.json`. The defaults suit a Mac
(`open` for the browser) and a client base in the four US time zones; `examples/voice-mod.json`
lists every key. To keep ticks across reloads and let the model read them, run the page
server in the project:

    python3 /path/to/claude-code-voice-mod/server/checklist_server.py --root .

Without it, checklist pages open as files and ticks stay in the browser.

Then rewrite `voice/voice.md` for yourself. The one here is an example for "Sam", a founder
of a small consultancy, so replace its rules with yours.

## Tests

    npm install
    npm test

That runs the TypeScript tests (31, on Vitest) and the Python tests for the checker (63) and
the page server (6). The TypeScript tests import `claude-code/testing`, so the same file also
runs under Claude Code's own runner:

    claude plugin validate .
    claude plugin test .

`npm test` maps that import to Vitest through `tests/shim/`, which is why it works without
Claude Code installed.

## Choices I made

The rules live in two files, `voice/voice.md` and the checker, and the hooks never restate
one. Two copies of a rule drift apart without anything noticing, so the voice file is read on
every prompt compose and the checker imports its word lists from `copy_rules.py`.

Nothing in the mod calls a model. `claude plugin validate` lists every engine call the mod
makes, and none of them is a model or agent call, so it adds no cost per turn.

The live fixer stays tiny on purpose. Anything that needs judgment goes to the after-turn
note so the model rewrites it itself; a live fixer that guessed could change what the reply means.

Feedback goes to the model as a private note instead of a warning in the chat. I checked
that a user-role row appended with `$.session.append` reaches the model and is never emitted
to the VS Code stream, so the note can be specific without cluttering the conversation.

The mod writes the session id to `loaded-sessions.txt` on start. A plain `UserPromptSubmit`
hook can check for it and warn when a session ran without the mod, which happened to me
when a Claude Code setting switched installed mods off and nothing looked different.

## Limitations

The checker is regex and word lists. It has no part-of-speech tagger, so it can't check
STE's multi-word noun rule or tell a verb from a noun in every case, and it only knows
English.

The client-facing folders and the email tools it gates are hard-coded in
`hooks/lib/gate.ts`. Gmail and Outlook through the claude.ai connectors are covered; other
mail tools are not.

The checklist page ships with the Stephens AI design system inlined. It works as is, but
you'll want to swap the styles for your own.

The eval measures what the checker can see. Whether a reply is actually better is your call
on the A/B page.

## Layout

    hooks/register.ts       the hooks and the three tools
    hooks/lib/              pure functions: compose, live fixes, gate, feedback, pages, clock, config
    checker/                voice_check.py, copy_rules.py, tests
    server/                 checklist_server.py, tests
    pages/                  the checklist page template
    voice/voice.md          the example voice
    eval/                   run_eval.py, build_ab_page.py, prompts, A/B template
    docs/CONTRACT.md        the interfaces between the pieces

MIT licensed. Copyright Stephens AI LLC.
