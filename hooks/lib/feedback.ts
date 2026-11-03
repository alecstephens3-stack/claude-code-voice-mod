// Turn the checker's verdict on a chat reply into a short private note for the
// model, and a metrics-only row for the score log. Pure functions.
//
// The note is appended as a user-role row the model reads and the panel never
// shows (measured on Claude Code 2.1.286: the row reached the model, and no event
// carried it to the VS Code stream). So it can be specific without cluttering the chat.

export type Issue = { code: string; msg?: string; excerpt?: string; sentence?: string; detail?: string }
export type SteBlock = { total: number; pass: number; score: number; target: number; issues: Issue[] }
export type Check = {
  surface: string
  ok: boolean
  fails: Issue[]
  notes: Issue[]
  verify?: string[]
  ste: { prose: SteBlock; steps: SteBlock }
  stats: { words: number; sentences: number; bullets: number; headers: number; questions: number }
}

const clip = (s: string | undefined, n = 90): string => {
  const t = (s ?? '').replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n - 1) + '…' : t
}

const examples = (issues: Issue[], n: number): string =>
  issues
    .slice(0, n)
    .map(i => `"${clip(i.excerpt ?? i.sentence)}"`)
    .join(', ')

const FAIL_LINES: Record<string, (xs: Issue[]) => string> = {
  PATH: xs => `It named file paths or file names (${examples(xs, 3)}). Describe the thing in words instead.`,
  HASH: xs => `It showed IDs or hashes (${examples(xs, 2)}). Leave them out.`,
  COMMAND: () => 'It handed the user a command. Run it yourself, or ask in words for what you need.',
  OPENER: () => 'It opened with a stock phrase. Start with the answer.',
  BANNED: xs => `It used banned words (${examples(xs, 3)}).`,
  DRAFT: xs =>
    `The draft in it breaks the rules for outgoing copy (${xs.map(x => clip(x.msg, 60)).slice(0, 2).join('; ')}). The user may paste it, so open your next reply with the corrected line in one short sentence.`,
}

const NOTE_LINES: Record<string, (xs: Issue[]) => string> = {
  FORMAT: () => 'It used headers or mostly bullets for a short answer. Write short answers as prose.',
  QUESTIONS: () => 'It asked more than one question. Ask at most one, after answering what you can.',
  PARA6: () => 'A paragraph ran past six sentences. Split it.',
}

const STE_WORDS: Record<string, string> = {
  LONG: 'too long',
  PASSIVE: 'passive',
  SEMICOLON: 'has a semicolon',
  MULTI: 'more than one instruction',
  CONDITION: 'condition after the command',
}

export function feedbackFor(c: Check): string | null {
  const lines: string[] = []

  const byCode = new Map<string, Issue[]>()
  for (const f of c.fails) byCode.set(f.code, [...(byCode.get(f.code) ?? []), f])
  for (const [code, xs] of byCode) {
    const line = FAIL_LINES[code]
    lines.push(line ? line(xs) : `${code}: ${clip(xs[0]?.msg)}`)
  }

  const steps = c.ste.steps
  if (steps.issues.length) {
    const shown = steps.issues
      .slice(0, 3)
      .map(i => `"${clip(i.sentence, 70)}" (${STE_WORDS[i.code] ?? i.code.toLowerCase()})`)
      .join(', ')
    lines.push(
      `Steps must follow strict STE: one command per sentence, condition first, 20 words or fewer, active voice. Fix: ${shown}.`,
    )
  }

  const prose = c.ste.prose
  if (prose.total >= 4 && prose.score < prose.target) {
    const counts = new Map<string, number>()
    for (const i of prose.issues) counts.set(i.code, (counts.get(i.code) ?? 0) + 1)
    const what = [...counts].map(([k, n]) => `${n} ${STE_WORDS[k] ?? k.toLowerCase()}`).join(', ')
    lines.push(
      `Only ${Math.round(prose.score * 100)}% of the sentences met the light STE check (target ${Math.round(prose.target * 100)}%): ${what}. Example: "${clip(prose.issues[0]?.sentence, 80)}".`,
    )
  }

  for (const n of c.notes) {
    const line = NOTE_LINES[n.code]
    if (line && !lines.includes(line([n]))) lines.push(line([n]))
  }

  if (!lines.length) return null
  return [
    "Writing check on your last reply. The user doesn't see this note. Apply it from your next reply on, and don't mention it.",
    ...lines.slice(0, 8).map(l => `- ${l}`),
  ].join('\n')
}

// One row per checked reply: numbers and codes only, never the words.
export function scoreRow(c: Check, at: string, sessionId: string): Record<string, unknown> {
  const count = (xs: Issue[]) => xs.reduce<Record<string, number>>((m, x) => ((m[x.code] = (m[x.code] ?? 0) + 1), m), {})
  return {
    at,
    session: sessionId,
    surface: c.surface,
    words: c.stats.words,
    fails: count(c.fails),
    notes: count(c.notes),
    prose: { total: c.ste.prose.total, pass: c.ste.prose.pass },
    steps: { total: c.ste.steps.total, pass: c.ste.steps.pass },
  }
}
