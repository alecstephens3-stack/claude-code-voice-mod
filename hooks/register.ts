// voice-mod: give the agent one writing voice, check what it writes, and move
// step-by-step browser tasks out of the chat into a checklist page.
//
// The parts (hooks run in the VS Code chat panel too; drawing does not, so this
// mod draws nothing):
//   prompt.compose  the voice section in the system prompt, terminal framing rewritten
//   turn.step       live fixes for slips with one right answer, before the user sees them
//   turn.complete   the after-reply check, a private note to the model, the score log
//   tool.call       the gate on outgoing copy (file writes, email drafts and sends)
//   prompt.submit   the clock line before every reply
//   tools           open_checklist, open_page, read_checklist
//
// The rules live in two places only: voice/voice.md (how the agent writes) and
// checker/voice_check.py (what is checked). Nothing here restates them.
//
// Every helper that touches the engine is a top-level function taking `$`, so
// `claude plugin validate` can list everything this mod does before it runs.

import type { Register, EngineInterface } from 'claude-code'
import { reshape } from './lib/compose'
import { createFixer, type Fixer } from './lib/live'
import { writeTarget, isEmailTool, emailBody, KEEP_MARKER, GUIDE_NAME, type Target } from './lib/gate'
import { feedbackFor, scoreRow, type Check } from './lib/feedback'
import { clockLine } from './lib/clock'
import { scheduleClaim, isScheduleLookup, scheduleNote } from './lib/schedule'
import { renderChecklist, checklistId, dates, ID_PATTERN, ticksAsText, cleanStep, type Step } from './lib/pages'
import { CONFIG_FILE, parseConfig, type Config } from './lib/config'

type Engine = EngineInterface

const SCORE_LOG_MAX_ROWS = 3000
const PAGE_ROOTS = /^(artifacts\/|clients\/[^/]+\/(artifacts|deliverables)\/)/

// Session state. A hot reload starts these over and runs session.start again.
let project: string | null = null // the project root, set only when the project opted in
let config: Config = parseConfig('{}')
let voice = ''
let checklistOpenedThisTurn = false
let scheduleLookedUp = false // a calendar or inbox lookup this turn (lib/schedule.ts)
// VOICE_MOD_EVAL=1: the before/after eval is running. Nothing opens in the browser,
// nothing reaches the page server, pages go to <dataDir>/eval-pages, no score rows.
let evalMode = false

// ---- helpers ------------------------------------------------------------------

async function loadVoice($: Engine): Promise<string> {
  try {
    return await $.fs.read(`${$.plugin.root}/voice/voice.md`)
  } catch {
    return ''
  }
}

// The mod acts only in a project that holds .claude/voice-mod.json. Anywhere else
// it stands aside.
async function findProject($: Engine): Promise<{ root: string; raw: string } | null> {
  const root = await $.session.root()
  try {
    return { root, raw: await $.fs.read(`${root}/${CONFIG_FILE}`) }
  } catch {
    return null
  }
}

function checkerPath($: Engine): string {
  return config.checker.startsWith('/') ? config.checker : `${$.plugin.root}/${config.checker}`
}

// Run the checker on some text. Null when it could not run: the mod never blocks
// the user's work because its own tooling is broken.
async function runCheck($: Engine, text: string, surface: string, html = false): Promise<Check | null> {
  if (!project) return null
  const argv = ['python3', checkerPath($), '-', '--surface', surface, '--json']
  if (html) argv.push('--html')
  for (const f of config.proofFiles) argv.push('--proof', `${project}/${f}`)
  try {
    const r = await $.process.run(argv, { cwd: project, stdin: text, timeoutMs: 20_000 })
    return JSON.parse(r.stdout) as Check
  } catch (err) {
    $.ui.log(`voice-mod: the writing check did not run (${String(err).slice(0, 120)})`)
    return null
  }
}

async function appendLine($: Engine, name: string, line: string, keep: number): Promise<void> {
  if (!project || evalMode) return
  const path = `${project}/${config.dataDir}/${name}`
  let old = ''
  try {
    old = await $.fs.read(path)
  } catch {}
  const rows = old.split('\n').filter(Boolean)
  rows.push(line)
  await $.fs.write(path, rows.slice(-keep).join('\n') + '\n')
}

// Proof of life: the session ids this mod loaded in. A plain UserPromptSubmit hook
// (which runs even when this mod does not) can look for the session here and warn
// when it is missing. I added it after a Claude Code setting switched installed mods
// off and several sessions ran without the mod while everything looked normal.
async function markLoaded($: Engine): Promise<void> {
  await appendLine($, 'loaded-sessions.txt', await $.session.id(), 500)
}

async function openInBrowser($: Engine, url: string): Promise<void> {
  if (evalMode || !project) return
  await $.process.run([...config.browser, url], { timeoutMs: 15_000 })
}

async function serverUp($: Engine): Promise<boolean> {
  if (evalMode) return false
  try {
    const r = await $.http.fetch(`${config.pageServer}/api/health`)
    return r.ok
  } catch {
    return false
  }
}

async function saveChecklistMeta($: Engine, id: string, title: string, items: { id: string; text: string }[]): Promise<void> {
  await $.http.fetch(`${config.pageServer}/api/checklist/${id}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ticks: {}, title, items }),
  })
}

// Write a checklist page, register it with the page server, open it. Returns the id
// and whether ticks will reach the server.
async function publishChecklist(
  $: Engine,
  args: { title: string; intro?: string; steps: Step[]; id?: string },
): Promise<{ id: string; server: boolean }> {
  const now = new Date()
  const id = checklistId(args.title, now, args.id, config.home.tz)
  const template = await $.fs.read(`${$.plugin.root}/pages/checklist-template.html`)
  const html = renderChecklist(template, {
    title: args.title,
    intro: args.intro,
    steps: args.steps,
    id,
    created: dates(now, config.home.tz).human,
  })
  const rel = evalMode ? `${config.dataDir}/eval-pages/${id}.html` : `${config.pagesDir}/${id}.html`
  await $.fs.write(`${project}/${rel}`, html)
  const server = await serverUp($)
  if (server) {
    const items = args.steps.map((s, i) => ({ id: `s${i + 1}`, text: cleanStep(s.text) }))
    await saveChecklistMeta($, id, args.title, items).catch(() => {})
  }
  await openInBrowser($, server ? `${config.pageServer}/pages/${rel}` : `file://${project}/${rel}`)
  return { id, server }
}

// Readable reasons for a refusal, at most eight lines.
function reasons(c: Check): string {
  const lines = c.fails.map(f => `- ${f.code}: ${f.msg ?? ''}${f.excerpt ? ` ("${f.excerpt.slice(0, 80)}")` : ''}`)
  const steps = c.ste.steps.issues.map(i => `- STEP ${i.code}: "${(i.sentence ?? '').slice(0, 90)}"${i.detail ? ` (${i.detail})` : ''}`)
  return [...lines, ...steps].slice(0, 8).join('\n')
}

// Fallback: a reply that still wrote a checklist into chat gets its page anyway.
async function autoChecklist($: Engine, answer: string): Promise<void> {
  if (!project || checklistOpenedThisTurn) return
  const boxes = answer.split('\n').filter(l => /^\s*[-*]\s*\[[ xX]\]\s+\S/.test(l))
  if (boxes.length < 3) return
  const heading = answer.split('\n').find(l => /^#{1,3}\s+\S/.test(l)) ?? ''
  const title = heading.replace(/^#+\s*/, '').trim() || 'Checklist'
  try {
    const { id } = await publishChecklist($, { title, steps: boxes.map(l => ({ text: cleanStep(l) })) })
    await $.session.append({
      message: {
        type: 'user',
        content: [
          {
            type: 'text',
            text: `The checklist in your last reply was opened in the browser as a page (id ${id}). Next time call open_checklist instead of writing checkboxes in chat. Don't mention this note.`,
          },
        ],
      },
    })
  } catch (err) {
    $.ui.log(`voice-mod: could not open the checklist page (${String(err).slice(0, 120)})`)
  }
}

async function registerTools($: Engine): Promise<void> {
  await $.tool.register({
    name: 'open_checklist',
    description:
      "Open a checklist in the user's browser as a page whose ticks save, so you can read them back with read_checklist. Use it whenever the user will carry out steps in their browser (a website, an account, a dashboard, a form to fill in), and put anything they paste in a step's copy box, never in chat. Next steps, to-dos and decisions stay in chat, never in a checklist. Each step must follow strict Simplified Technical English: one command per sentence, condition first, 20 words or fewer, active voice, no semicolons. Steps that break the rules come back for a rewrite. Returns the checklist id.",
    inputSchema: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Short title, e.g. "Install the invoice app on the bookkeeper\'s PC"' },
        intro: { type: 'string', description: 'One optional sentence on what this is for' },
        steps: {
          type: 'array',
          minItems: 1,
          items: {
            type: 'object',
            properties: {
              text: { type: 'string', description: 'The step, as one STE command' },
              detail: { type: 'string', description: 'Optional short note under the step' },
              copy: { type: 'string', description: 'Optional text the user pastes (a form answer, an email, a value). Shown in a box with its own Copy button. Not checked as STE.' },
            },
            required: ['text'],
          },
        },
        id: { type: 'string', description: 'Optional id to update an existing checklist (lowercase, dashes)' },
      },
      required: ['title', 'steps'],
    },
  })
  await $.tool.register({
    name: 'open_page',
    description:
      "Open an HTML page from the project in the user's browser. Use it for plans and reports. The page must sit under artifacts/ or a client's artifacts or deliverables folder, and it is checked against the writing rules first.",
    inputSchema: {
      type: 'object',
      properties: { path: { type: 'string', description: 'Path of the .html file, absolute or from the project root' } },
      required: ['path'],
    },
  })
  await $.tool.register({
    name: 'read_checklist',
    description: 'Read which steps the user has ticked on a checklist opened with open_checklist.',
    inputSchema: {
      type: 'object',
      properties: { id: { type: 'string', description: 'The checklist id open_checklist returned' } },
      required: ['id'],
    },
  })
}

// ---- hooks ----------------------------------------------------------------------

export const register: Register = on => {
  // The voice, in the system prompt.
  on('session.start', async ($, e, next) => {
    const found = await findProject($)
    project = found?.root ?? null
    config = parseConfig(found?.raw ?? '{}', Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
    evalMode = (await $.env.get('VOICE_MOD_EVAL')) === '1'
    voice = project ? await loadVoice($) : ''
    if (project) await registerTools($)
    await markLoaded($).catch(() => {})
    return next(e)
  })

  on('prompt.compose', async ($, e, next) => {
    const r = await next(e)
    if (!project || e.traits.includes('bare') || e.traits.includes('teammate')) return r
    // Read on every compose, so an edit to voice.md lands on the next request.
    voice = (await loadVoice($)) || voice
    return { ...r, sections: reshape(r.sections, voice).sections }
  })

  // Live fixes, main conversation only.
  on('turn.step', async function* ($, e, next) {
    const stream = next(e)
    if (!project || e.agentId) return yield* stream
    const fixers = new Map<number, Fixer>()
    const flushAll = function* () {
      for (const [index, f] of fixers) {
        const rest = f.flush()
        if (rest) yield { kind: 'text' as const, index, text: rest }
      }
      fixers.clear()
    }
    let r = await stream.next()
    while (!r.done) {
      const c = r.value
      if (c.kind === 'text') {
        let f = fixers.get(c.index)
        if (!f) fixers.set(c.index, (f = createFixer()))
        const out = f.push(c.text)
        if (out) yield { kind: 'text' as const, index: c.index, text: out }
      } else {
        // Anything else (a block's end, a tool call) first releases what is held.
        yield* flushAll()
        yield c
      }
      r = await stream.next()
    }
    yield* flushAll()
    return r.value
  })

  on('turn.start', async ($, e, next) => {
    checklistOpenedThisTurn = false
    scheduleLookedUp = false
    return next(e)
  })

  // The clock, when the user sends a prompt and before it is stored, so the line sits
  // just ahead of their message in the first request: a row the model reads and the
  // panel never shows (the same channel as the writing notes). Appended at turn.start
  // instead, the first request of the turn did not carry it.
  on('prompt.submit', async ($, e, next) => {
    if (project) {
      const text = clockLine(new Date(), config.home, config.clientZones)
      await $.session.append({ message: { type: 'user', content: [{ type: 'text', text }] } }).catch(() => {})
    }
    return next(e)
  })

  // The check after each reply.
  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    if (!project || e.agentId || e.reason !== 'answer' || !e.answer.trim()) return r
    await autoChecklist($, e.answer)
    const claim = scheduleLookedUp ? null : scheduleClaim(e.answer)
    if (claim) {
      await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: scheduleNote(claim) }] } }).catch(() => {})
    }
    const check = await runCheck($, e.answer, 'chat')
    if (!check) return r
    const sessionId = await $.session.id()
    await appendLine($, 'scores.jsonl', JSON.stringify(scoreRow(check, new Date().toISOString(), sessionId)), SCORE_LOG_MAX_ROWS).catch(() => {})
    const note = feedbackFor(check)
    if (note) {
      await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: note }] } }).catch(() => {})
    }
    return r
  })

  // The gate on outgoing copy.
  on('tool.call', async ($, e, next) => {
    if (!project) return next(e)
    const { tool, tool_use_id, ...input } = e as unknown as Record<string, unknown> & { tool: string }
    if (!e.agentId && isScheduleLookup(tool, input)) scheduleLookedUp = true

    let target: Target | null = null
    if (tool === 'Write' || tool === 'Edit') {
      target = writeTarget(tool, input, project)
    } else if (isEmailTool(tool)) {
      const body = emailBody(input)
      if (body) target = { surface: 'email', text: body.text, html: body.html, label: 'the email', gated: true }
    }
    if (!target) return next(e)

    const check = await runCheck($, target.text, target.surface, target.html)
    if (!check) return next(e)
    const kept = KEEP_MARKER.test(target.text)

    if (!check.ok && target.gated && !kept) {
      return {
        deny: `The writing check stopped ${target.label} before it was saved or sent. Fix these, then try again:\n${reasons(check)}\nIf a line must stay word for word (the user's own line or a client's quote), add a comment "voice-check: keep <reason>" next to it.`,
      }
    }

    const ran = await next(e)
    // Allowed: anything still worth fixing rides along as a note only the model reads.
    const soft = [
      ...check.ste.steps.issues.map(i => `step "${(i.sentence ?? '').slice(0, 80)}" (${i.code.toLowerCase()})`),
      ...(check.ste.prose.total >= 4 && check.ste.prose.score < check.ste.prose.target
        ? [`only ${Math.round(check.ste.prose.score * 100)}% of sentences met the light STE check`]
        : []),
      ...(kept ? check.fails.map(f => `kept on purpose: ${f.code}`) : []),
    ]
    if (!soft.length || ran.deny !== undefined || ran.isError) return ran
    return { ...ran, context: [...(ran.context ?? []), `Writing check on ${target.label}: ${soft.slice(0, 6).join('; ')}.`] }
  })

  // Pages in the browser.
  on('tool.call', { tool: 'mcp__voice-mod__open_checklist' }, async ($, e) => {
    if (!project) return { deny: `voice-mod is off in this project (no ${CONFIG_FILE}).` }
    const input = e as unknown as { title?: unknown; intro?: unknown; steps?: unknown; id?: unknown }
    const title = String(input.title ?? '').trim()
    const steps: Step[] = (Array.isArray(input.steps) ? input.steps : [])
      .map((s: unknown): Step =>
        typeof s === 'string'
          ? { text: s }
          : {
              text: String((s as Step)?.text ?? ''),
              detail: (s as Step)?.detail ? String((s as Step).detail) : undefined,
              copy: (s as Step)?.copy ? String((s as Step).copy) : undefined,
            },
      )
      .filter(s => cleanStep(s.text))
    if (!title || !steps.length) return { deny: 'A checklist needs a title and at least one step.' }

    const check = await runCheck($, steps.map(s => cleanStep(s.text)).join('\n'), 'steps')
    if (check && !check.ok) {
      return { deny: `Not opened. These steps break strict STE. Rewrite them and call again:\n${reasons(check)}` }
    }
    const { id, server } = await publishChecklist($, {
      title,
      intro: typeof input.intro === 'string' ? input.intro : undefined,
      steps,
      id: typeof input.id === 'string' ? input.id : undefined,
    })
    checklistOpenedThisTurn = true
    return {
      result: `Opened "${title}" in the browser (${steps.length} steps, id ${id}).${server || evalMode ? '' : " The page server is offline, so ticks save only in the browser and read_checklist can't see them."} In chat, say what it is and the one thing to do first.`,
    }
  })

  on('tool.call', { tool: 'mcp__voice-mod__open_page' }, async ($, e) => {
    if (!project) return { deny: `voice-mod is off in this project (no ${CONFIG_FILE}).` }
    const raw = String((e as unknown as { path?: unknown }).path ?? '').trim()
    const rel = raw.startsWith(project + '/') ? raw.slice(project.length + 1) : raw.replace(/^\.\//, '')
    if (!rel.endsWith('.html') || rel.includes('..') || !PAGE_ROOTS.test(rel)) {
      return { deny: 'open_page takes an .html page under artifacts/ or a client artifacts or deliverables folder.' }
    }
    let html: string
    try {
      html = await $.fs.read(`${project}/${rel}`)
    } catch {
      return { deny: `There is no page at ${rel}.` }
    }
    if (config.designFingerprint && !new RegExp(config.designFingerprint).test(html)) {
      return { deny: 'That page is not built on the project design system. Build it on the design system first.' }
    }
    const check = await runCheck($, html, GUIDE_NAME.test(rel) ? 'guide' : 'artifact', true)
    if (check && !check.ok && !KEEP_MARKER.test(html)) {
      return { deny: `Not opened. The page breaks the writing rules. Fix these first:\n${reasons(check)}` }
    }
    const server = await serverUp($)
    await openInBrowser($, server ? `${config.pageServer}/pages/${rel}` : `file://${project}/${rel}`)
    return { result: 'Opened it in the browser. In chat, say what it is and the one thing to do first.' }
  })

  on('tool.call', { tool: 'mcp__voice-mod__read_checklist' }, async ($, e) => {
    const id = String((e as unknown as { id?: unknown }).id ?? '')
    if (!ID_PATTERN.test(id)) return { deny: 'That is not a checklist id.' }
    try {
      const r = await $.http.fetch(`${config.pageServer}/api/checklist/${id}`)
      if (!r.ok) return { deny: `The page server answered ${r.status}.` }
      return { result: ticksAsText(JSON.parse(r.text)) }
    } catch {
      return { deny: "The page server is offline, so the ticks can't be read. Ask the user in words." }
    }
  })
}
