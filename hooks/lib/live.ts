// Live fixes: the few slips that have exactly one right answer, fixed in the stream
// before the user sees them. Measured on 2.1.286: what a `turn.step` hook yields is the
// text the VS Code panel streams and the transcript keeps (a test swapped one word
// and the panel's stream, final message and result all carried the swap).
//
// Deliberately small. Anything that needs judgment (a file path, a long sentence, a
// passive) goes to the after-turn check instead, which tells the model and lets it
// rewrite in its own words. A fixer that guesses would put words in its mouth.
//
// Text is held only until a word boundary, so streaming stays live. Code fences and
// inline code pass through untouched.

const OPENER = /^(\s*)(?:certainly|absolutely|great question|sure thing)\s*[!.,:]?\s+/i

// "utilize" and family, case kept on the first letter.
const UTILIZE: ReadonlyArray<readonly [RegExp, string]> = [
  [/\butilization\b/gi, 'use'],
  [/\butilizing\b/gi, 'using'],
  [/\butilized\b/gi, 'used'],
  [/\butilizes\b/gi, 'uses'],
  [/\butilize\b/gi, 'use'],
]

function keepCase(found: string, replacement: string): string {
  return found[0] === found[0]!.toUpperCase() ? replacement[0]!.toUpperCase() + replacement.slice(1) : replacement
}

// Prose only: fixes outside inline code spans.
export function fixProse(text: string): string {
  return text
    .split(/(`[^`\n]*`)/)
    .map((part, i) => {
      if (i % 2 === 1) return part
      let out = part
      for (const [pattern, word] of UTILIZE) out = out.replace(pattern, m => keepCase(m, word))
      return out
    })
    .join('')
}

export function stripOpener(text: string): string {
  const m = OPENER.exec(text)
  if (!m) return text
  const rest = text.slice(m[0].length)
  return m[1] + rest.charAt(0).toUpperCase() + rest.slice(1)
}

export type Fixer = {
  push(text: string): string
  flush(): string
}

// One fixer per text block. `push` returns what is safe to show now; `flush`
// returns the rest when the block ends.
export function createFixer(): Fixer {
  let held = ''
  let inFence = false
  let atStart = true

  // Fix one complete stretch of text, line by line, tracking code fences.
  const fixLines = (text: string): string =>
    text
      .split(/(\n)/)
      .map(piece => {
        if (piece === '\n') return piece
        if (/^\s*```/.test(piece)) {
          inFence = !inFence
          return piece
        }
        return inFence ? piece : fixProse(piece)
      })
      .join('')

  const release = (text: string): string => {
    let out = text
    if (atStart) {
      out = stripOpener(out)
      atStart = false
    }
    return fixLines(out)
  }

  return {
    push(text: string): string {
      held += text
      // At the very start, hold long enough to see a whole opener.
      if (atStart && held.trimStart().length < 24 && !/[\n.!?]/.test(held.trimStart())) return ''
      // Release up to the last whitespace that is outside an open inline code span.
      let cut = -1
      let ticks = 0
      for (let i = 0; i < held.length; i++) {
        const c = held[i]
        if (c === '`') ticks += 1
        else if ((c === ' ' || c === '\n' || c === '\t') && ticks % 2 === 0) cut = i + 1
      }
      // A fence line has to arrive whole before it is judged.
      const lastNewline = held.lastIndexOf('\n')
      const tail = held.slice(lastNewline + 1)
      if (/^\s*`{1,3}$/.test(tail) || /^\s*```/.test(tail)) cut = Math.min(cut, lastNewline + 1)
      if (cut <= 0) return ''
      const ready = held.slice(0, cut)
      held = held.slice(cut)
      return release(ready)
    },
    flush(): string {
      const rest = held
      held = ''
      return rest ? release(rest) : ''
    },
  }
}
