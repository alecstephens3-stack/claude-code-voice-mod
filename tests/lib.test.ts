import { test, expect, describe } from 'claude-code/testing'
import { reshape, rewriteBuiltIn, VOICE_ID } from '../hooks/lib/compose'
import { createFixer, fixProse, stripOpener } from '../hooks/lib/live'
import { surfaceForPath, writeTarget, isEmailTool, emailBody, KEEP_MARKER } from '../hooks/lib/gate'
import { renderChecklist, checklistId, cleanStep, ticksAsText, escapeHtml, ID_PATTERN } from '../hooks/lib/pages'
import { feedbackFor, scoreRow, type Check } from '../hooks/lib/feedback'
import { clockLine, isOpen } from '../hooks/lib/clock'
import { parseConfig, defaults, US_ZONES } from '../hooks/lib/config'
import { scheduleClaim, isScheduleLookup, scheduleNote } from '../hooks/lib/schedule'

// The lean body's writing lines as Claude Code 2.1.286 composes them (measured 2026-10-03).
const LEAN = [
  'You are an agent working with the user toward their goals.',
  '# Harness',
  ' - Text you output outside of tool use is displayed to the user as Github-flavored markdown in a terminal.',
  ' - Prefer the dedicated file/search tools over shell commands when one fits.',
  " - Reference code as `file_path:line_number` — it's clickable.",
].join('\n')

describe('compose', () => {
  test('rewrites the terminal framing and the file reference line', async () => {
    const r = rewriteBuiltIn(LEAN)
    expect(r.applied).toBe(2)
    expect(r.text).not.toContain('in a terminal')
    expect(r.text).not.toContain('file_path:line_number')
    expect(r.text).toContain("the user's chat panel")
    expect(r.text).toContain('Prefer the dedicated file/search tools')
  })

  test('places the voice first among the session sections, once', async () => {
    const sections = [
      { id: 'lean_body', text: LEAN, scope: 'shared' as const },
      { id: 'communication', text: 'x', scope: 'session' as const },
      { id: 'memory', text: 'y', scope: 'session' as const },
    ]
    const once = reshape(sections, '# How you write\nBe warm.').sections
    const twice = reshape(once, '# How you write\nBe warm.').sections
    expect(twice.map(s => s.id)).toEqual(['lean_body', VOICE_ID, 'communication', 'memory'])
    expect(twice[1]!.scope).toBe('session')
    expect(twice[0]!.text).toContain("the user's chat panel")
  })

  test('an empty voice adds nothing', async () => {
    const out = reshape([{ id: 'memory', text: 'y', scope: 'session' }], '  ').sections
    expect(out.map(s => s.id)).toEqual(['memory'])
  })
})

describe('live fixes', () => {
  const run = (chunks: string[]): string => {
    const f = createFixer()
    return chunks.map(c => f.push(c)).join('') + f.flush()
  }

  test('strips a stock opener split across chunks', async () => {
    expect(run(['Cert', 'ainly! ', 'The install', ' went fine and Sam has four bills in.'])).toBe(
      'The install went fine and Sam has four bills in.',
    )
  })

  test('replaces utilize forms, keeping the first letter case', async () => {
    expect(fixProse('Utilize the app. We utilized it and keep utilizing it.')).toBe('Use the app. We used it and keep using it.')
  })

  test('leaves code fences and inline code alone', async () => {
    const text = 'Use `utilize()` here.\n```js\nconst x = utilize(1)\n```\nWe utilize nothing.'
    expect(run([text.slice(0, 17), text.slice(17, 40), text.slice(40)])).toBe(
      'Use `utilize()` here.\n```js\nconst x = utilize(1)\n```\nWe use nothing.',
    )
  })

  test('passes ordinary text through unchanged, whatever the chunking', async () => {
    const text = 'Sam installed it on Thursday. Absolutely nothing broke, and the four bills are in QuickBooks.'
    const pieces = text.match(/.{1,7}/gs) ?? []
    expect(run(pieces)).toBe(text)
  })

  test('only strips an opener at the very start', async () => {
    expect(stripOpener('Absolutely, that works.')).toBe('That works.')
    expect(stripOpener('It works. Absolutely.')).toBe('It works. Absolutely.')
  })
})

describe('gate', () => {
  const V = '/home/dev/example-project'

  test('client-facing places get a surface, internal ones do not', async () => {
    expect(surfaceForPath('clients/acme/deliverables/invoice-app-install-guide.html')).toEqual({ surface: 'guide', gated: true })
    expect(surfaceForPath('clients/acme/artifacts/install-prep-notes-2026-10-01.html')).toEqual({ surface: 'artifact', gated: true })
    expect(surfaceForPath('content/linkedin/2026-10-03-post.md')).toEqual({ surface: 'social', gated: true })
    expect(surfaceForPath('content/social/launch.md')).toEqual({ surface: 'social', gated: true })
    expect(surfaceForPath('clients/acme/notes.md')).toBe(null)
    expect(surfaceForPath('research/2026-10-03-ste100.md')).toBe(null)
    expect(surfaceForPath('artifacts/checklists/x.html')).toBe(null)
    expect(surfaceForPath('archives/old/page.html')).toBe(null)
  })

  test('a Write outside the project is not checked', async () => {
    expect(writeTarget('Write', { file_path: '/tmp/x.html', content: 'Hi — there' }, V)).toBe(null)
  })

  test('an Edit checks only the new text', async () => {
    const t = writeTarget('Edit', { file_path: `${V}/clients/acme/deliverables/guide.html`, old_string: 'a', new_string: '<p>New — text</p>' }, V)
    expect(t?.text).toBe('<p>New — text</p>')
    expect(t?.surface).toBe('guide')
    expect(t?.html).toBe(true)
  })

  test('email tools and their bodies', async () => {
    expect(isEmailTool('mcp__claude_ai_Gmail__create_draft')).toBe(true)
    expect(isEmailTool('mcp__claude_ai_Gmail__search_threads')).toBe(false)
    expect(emailBody({ to: 'sam@example.com', body: 'Hey Sam,\n\nAll set.' })).toEqual({ text: 'Hey Sam,\n\nAll set.', html: false })
    expect(emailBody({ htmlBody: '<p>Hi</p>' })?.html).toBe(true)
  })

  test('the keep marker needs a reason', async () => {
    expect(KEEP_MARKER.test('<!-- voice-check: keep owner-verbatim -->')).toBe(true)
    expect(KEEP_MARKER.test('<!-- voice-check: keep -->')).toBe(false)
  })
})

describe('pages', () => {
  const T = '<title>{{TITLE}}</title><h1>{{TITLE}}</h1><p class="intro">{{INTRO}}</p><ol data-id="{{CHECKLIST_ID}}">{{ITEMS}}</ol><footer>CHECKLIST · {{CREATED}}</footer>'

  test('fills every placeholder and escapes the text', async () => {
    const html = renderChecklist(T, {
      title: 'Install <Invoice App>',
      intro: '',
      steps: [{ text: '1. Open the app.' }, { text: '- [ ] Click Close.', detail: 'Only once.' }],
      id: 'install-invoice-app-2026-10-03',
      created: '3 Oct 2026',
    })
    expect(html).not.toContain('{{')
    expect(html).toContain('Install &lt;Invoice App&gt;')
    expect(html).toContain('<li class="ck-item" data-id="s1"><label><input type="checkbox" id="s1"><span class="ck-text">Open the app.</span></label></li>')
    expect(html).toContain('<span class="ck-text">Click Close.</span><span class="ck-detail">Only once.</span></label></li>')
  })

  test('ids are safe and dated in the user\'s time zone', async () => {
    const now = new Date('2026-10-02T23:30:00Z')
    expect(checklistId("Sam's install: day 2!", now, undefined, 'Europe/London')).toBe('sam-s-install-day-2-2026-10-03')
    expect(checklistId("Sam's install: day 2!", now, undefined, 'America/Chicago')).toBe('sam-s-install-day-2-2026-10-02')
    const id = checklistId("Sam's install: day 2!", now)
    expect(ID_PATTERN.test(id)).toBe(true)
    expect(checklistId('x', new Date(), 'BAD ID')).not.toBe('BAD ID')
  })

  test('cleanStep and ticksAsText', async () => {
    expect(cleanStep('  - [x] 3) Send the guide.')).toBe('Send the guide.')
    const text = ticksAsText({ title: 'Install', items: [{ id: 's1', text: 'Open it.' }, { id: 's2', text: 'Close it.' }], ticks: { s1: true }, updated: null })
    expect(text).toBe('Install: 1 of 2 done\n[x] 1. Open it.\n[ ] 2. Close it.')
    expect(escapeHtml(`"a" & 'b'`)).toBe('&quot;a&quot; &amp; &#39;b&#39;')
  })
})

describe('feedback', () => {
  const clean: Check = {
    surface: 'chat',
    ok: true,
    fails: [],
    notes: [],
    ste: { prose: { total: 6, pass: 6, score: 1, target: 0.8, issues: [] }, steps: { total: 0, pass: 0, score: 1, target: 1, issues: [] } },
    stats: { words: 120, sentences: 6, bullets: 0, headers: 0, questions: 0 },
  }

  test('a clean reply gets no note', async () => {
    expect(feedbackFor(clean)).toBe(null)
  })

  test('paths, a weak STE score and a bad step each get one line', async () => {
    const note = feedbackFor({
      ...clean,
      ok: false,
      fails: [{ code: 'PATH', msg: 'file path', excerpt: 'clients/acme/notes.md' }],
      ste: {
        prose: { total: 10, pass: 6, score: 0.6, target: 0.8, issues: [{ code: 'LONG', sentence: 'A very long sentence here.', detail: '31 words' }] },
        steps: { total: 2, pass: 1, score: 0.5, target: 1, issues: [{ code: 'MULTI', sentence: 'Open the app and click Close.' }] },
      },
    })
    expect(note).toContain("The user doesn't see this note")
    expect(note).toContain('clients/acme/notes.md')
    expect(note).toContain('60%')
    expect(note).toContain('more than one instruction')
  })

  test('the score row has numbers and codes, never words', async () => {
    const row = scoreRow({ ...clean, fails: [{ code: 'PATH', excerpt: 'secret/path.md' }] }, '2026-10-03T00:00:00Z', 's1')
    expect(JSON.stringify(row)).not.toContain('secret')
    expect(row.fails).toEqual({ PATH: 1 })
  })
})

describe('clock', () => {
  const LONDON = { name: 'London', tz: 'Europe/London' }

  test('Saturday afternoon in London is a US Saturday morning, closed everywhere', async () => {
    const line = clockLine(new Date('2026-10-03T13:00:00Z'), LONDON)
    expect(line).toContain('Saturday 3 Oct, 2:00 PM in London')
    expect(line).toContain('For clients it is Saturday 3 Oct: 9:00 AM Eastern (EDT), 8:00 AM Central (CDT), 7:00 AM Mountain (MDT), 6:00 AM Pacific (PDT)')
    expect(line).toContain('Client business hours: closed everywhere (weekend)')
  })

  test('a Monday morning: open in the east, not yet in the west', async () => {
    const line = clockLine(new Date('2026-10-05T13:30:00Z'), LONDON)
    expect(line).toContain('Client business hours: open in Eastern and Central, closed elsewhere')
  })

  test('daylight saving ends a week apart in London and Chicago', async () => {
    // London leaves summer time on 25 Oct 2026, Chicago on 1 Nov.
    const between = clockLine(new Date('2026-10-28T14:30:00Z'), LONDON)
    expect(between).toContain('Wednesday 28 Oct, 2:30 PM in London')
    expect(between).toContain('9:30 AM Central (CDT)')
    const after = clockLine(new Date('2026-11-02T14:30:00Z'), LONDON)
    expect(after).toContain('Monday 2 Nov, 2:30 PM in London')
    expect(after).toContain('8:30 AM Central (CST)')
  })

  test('client zones come from the config', async () => {
    const line = clockLine(new Date('2026-10-05T09:00:00Z'), LONDON, [{ name: 'Berlin', tz: 'Europe/Berlin' }])
    expect(line).toContain('11:00 AM Berlin')
    expect(line).toContain('Client business hours: open in every zone')
  })

  test('business hours', async () => {
    expect(isOpen('Monday', 7)).toBe(false)
    expect(isOpen('Monday', 8)).toBe(true)
    expect(isOpen('Friday', 17)).toBe(false)
    expect(isOpen('Sunday', 10)).toBe(false)
  })
})

describe('schedule guard', () => {
  test('catches what a missed lookup sounds like', async () => {
    expect(scheduleClaim("**The demo with Dr. Lee needs a new date.** It didn't happen on Wednesday.")).toBe('needs a new date')
    expect(scheduleClaim('Priya hasn’t replied yet.')).not.toBeNull()
    expect(scheduleClaim('No invite yet from Sam.')).not.toBeNull()
    expect(scheduleClaim('Nothing new has come in from Priya.')).not.toBeNull()
  })

  test('leaves ordinary meeting talk alone', async () => {
    expect(scheduleClaim('The podcast records at 23:00 your time, on Teams.')).toBeNull()
    expect(scheduleClaim('Priya’s demo is Tue 13 Oct at 8:15 AM Central.')).toBeNull()
  })

  test('counts calendar and inbox lookups, through connectors or gws', async () => {
    expect(isScheduleLookup('mcp__claude_ai_Google_Calendar__list_events', {})).toBe(true)
    expect(isScheduleLookup('mcp__claude_ai_Gmail__search_threads', {})).toBe(true)
    expect(isScheduleLookup('Bash', { command: 'GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/gws-work gws calendar events list' })).toBe(true)
    expect(isScheduleLookup('Bash', { command: 'gws drive files list' })).toBe(false)
    expect(isScheduleLookup('Read', { file_path: 'context/current-priorities.md' })).toBe(false)
  })

  test('the note names the phrase and asks for a look', async () => {
    const n = scheduleNote('needs a new date')
    expect(n).toContain('"needs a new date"')
    expect(n).toContain('Search the calendar and the inbox')
  })
})

describe('checklist copy boxes', () => {
  test('a step with copy text gets its own paste box outside the label', () => {
    const html = renderChecklist('<ul>{{ITEMS}}</ul>', {
      title: 'T', id: 'x', created: '12 Oct 2026',
      steps: [{ text: 'Paste the answer.', copy: 'Line one\nLine <two>' }, { text: 'Click Submit.' }],
    })
    expect(html).toContain('<pre class="ck-paste-text">Line one\nLine &lt;two&gt;</pre>')
    expect(html).toContain('</label><div class="ck-paste">')
    expect(html.split('ck-paste-btn').length).toBe(2)
  })
})

describe('config', () => {
  test('no file or an empty file gives the defaults', () => {
    expect(parseConfig('')).toEqual(defaults())
    expect(parseConfig('{}', 'Europe/London').home).toEqual({ name: 'your time zone', tz: 'Europe/London' })
    expect(parseConfig('not json')).toEqual(defaults())
  })

  test('valid keys override, wrong types and unsafe paths are ignored', () => {
    const c = parseConfig(JSON.stringify({
      browser: ['xdg-open'],
      pageServer: 'http://127.0.0.1:8123/',
      pagesDir: '../outside',
      dataDir: './.voice-data/',
      proofFiles: ['docs/portfolio.md', '../secret.md'],
      designFingerprint: '(',
      home: { name: 'Denver', tz: 'America/Denver' },
      clientZones: 'Eastern',
    }))
    expect(c.browser).toEqual(['xdg-open'])
    expect(c.pageServer).toBe('http://127.0.0.1:8123')
    expect(c.pagesDir).toBe('artifacts/checklists')
    expect(c.dataDir).toBe('.voice-data')
    expect(c.proofFiles).toEqual(['docs/portfolio.md'])
    expect(c.designFingerprint).toBe('')
    expect(c.home.tz).toBe('America/Denver')
    expect(c.clientZones).toEqual(US_ZONES)
  })
})
