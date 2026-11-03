// The system prompt half of the mod: rewrite the few built-in lines that frame
// the agent as a terminal tool, and seat the voice section where the model reads
// it as instruction (the system prompt), not as a user message (CLAUDE.md).
//
// Measured against Claude Code 2.1.286: in the VS Code panel the model gets the
// short body (`lean_body`), whose only writing lines are the two rewritten below. The full
// prompt's `system` and `tone` sections carry their own versions, rewritten too, so
// the mod still holds if a server flag switches the session to the full prompt.
// The editor's own appended block ("use markdown link syntax") never reaches
// `prompt.compose`; the voice section answers it instead (its last line).

import type { PromptComposeSection } from 'claude-code'

export const VOICE_ID = 'voice-mod:voice'

// Each entry: a pattern for a built-in line, and what the model reads instead.
// A pattern that stops matching after an update is harmless (nothing rewritten),
// and `rewritesApplied` lets the tests and the debug line say so.
const REWRITES: ReadonlyArray<readonly [RegExp, string]> = [
  [
    /Text you output outside of tool use is displayed to the user as Github-flavored markdown in a terminal\./,
    "Text you write outside of tool use appears in the user's chat panel as rendered markdown. Write for a reader who runs the business and does not read code.",
  ],
  [
    /^[ \t]*- Reference code as `file_path:line_number`[^\n]*\n?/m,
    ' - Link a spot in a file only when the user has to open it. Otherwise leave file references out.\n',
  ],
  [
    /You can use Github-flavored markdown for formatting, and will be rendered in a monospace font using the CommonMark specification\./,
    "You can use markdown for formatting; it renders in the chat panel. Use as little as clarity allows.",
  ],
  [/^[ \t]*- Your responses should be short and concise\.[^\n]*\n?/m, ' - Match the length of a reply to the question.\n'],
  [
    /^[ \t]*- When referencing specific functions or pieces of code include the pattern file_path:line_number[^\n]*\n?/m,
    ' - Name a file or function only when the user has to go there.\n',
  ],
]

const REWRITTEN_SECTIONS = new Set(['lean_body', 'system', 'tone', 'intro'])

export function rewriteBuiltIn(text: string): { text: string; applied: number } {
  let out = text
  let applied = 0
  for (const [pattern, replacement] of REWRITES) {
    if (pattern.test(out)) {
      out = out.replace(pattern, replacement)
      applied += 1
    }
  }
  return { text: out, applied }
}

// The sections as the engine composed them, with the built-in lines rewritten and
// the voice section placed first among the session sections (every `shared`
// section must stay ahead of every `session` one).
export function reshape(
  sections: readonly PromptComposeSection[],
  voice: string,
): { sections: PromptComposeSection[]; applied: number } {
  let applied = 0
  const out: PromptComposeSection[] = sections
    .filter(s => s.id !== VOICE_ID)
    .map(s => {
      if (!REWRITTEN_SECTIONS.has(s.id)) return s
      const r = rewriteBuiltIn(s.text)
      applied += r.applied
      return r.applied ? { ...s, text: r.text } : s
    })
  if (voice.trim()) {
    const firstSession = out.findIndex(s => s.scope === 'session')
    const at = firstSession === -1 ? out.length : firstSession
    out.splice(at, 0, { id: VOICE_ID, text: voice.trim(), scope: 'session' })
  }
  return { sections: out, applied }
}
