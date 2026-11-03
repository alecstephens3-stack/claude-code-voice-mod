// Which tool calls carry words that leave the building, and which writing rules
// apply to them. Pure functions; register.ts runs the check and acts on it.
//
// In scope: chat replies, client emails and messages, client guides and documents,
// website and social copy. Notes, research, logs and code are internal and never
// gated here.

export type Surface = 'chat' | 'email' | 'guide' | 'artifact' | 'social' | 'steps'

export type Target = {
  surface: Surface
  text: string
  html: boolean
  label: string // what the model is told it was, in words
  gated: boolean // false: report only, never refuse
}

// A client how-to: strict STE on every step. Names only; "install" alone is not
// enough ("install-prep-notes" is a prep page, not a guide).
export const GUIDE_NAME = /(guide|manual|how-?to|walkthrough|instructions|training)/i

// The client-facing places in a project, relative to its root. This is the folder
// layout the mod assumes; change these patterns to match yours.
export function surfaceForPath(rel: string): { surface: Surface; gated: boolean } | null {
  const p = rel.replace(/\\/g, '/')
  if (p.startsWith('archives/') || p.includes('/archive/')) return null
  const name = p.split('/').pop() ?? ''
  const isHtml = name.endsWith('.html')
  const isText = /\.(md|txt)$/.test(name)

  if (/^content\/(linkedin|social)\//.test(p) && (isText || isHtml)) return { surface: 'social', gated: true }
  if (/^clients\/[^/]+\/deliverables\//.test(p) && (isHtml || isText)) {
    return { surface: GUIDE_NAME.test(name) ? 'guide' : 'artifact', gated: true }
  }
  if (/^(clients\/[^/]+\/artifacts|artifacts)\//.test(p) && isHtml) {
    if (p.startsWith('artifacts/checklists/')) return null // the default pagesDir: written by this mod, already checked
    return { surface: GUIDE_NAME.test(name) ? 'guide' : 'artifact', gated: true }
  }
  return null
}

const EMAIL_TOOL =
  /^mcp__claude_ai_(Gmail__(create_draft|update_draft|send_message|reply|forward)|Microsoft_365__outlook_(create_draft|create_reply_draft|create_reply_all_draft|update_draft|send_mail|forward_mail))$/

const BODY_KEYS = ['body', 'htmlBody', 'html_body', 'text', 'content', 'message', 'comment']

export function isEmailTool(tool: string): boolean {
  return EMAIL_TOOL.test(tool)
}

// The words an email tool would send, from whichever field carries them.
export function emailBody(input: Record<string, unknown>): { text: string; html: boolean } | null {
  for (const key of BODY_KEYS) {
    const v = input[key]
    if (typeof v === 'string' && v.trim()) {
      return { text: v, html: /html/i.test(key) || /<(p|div|br|table|span)\b/i.test(v) }
    }
  }
  return null
}

// A file write or edit as a check target, or null when the path is internal.
export function writeTarget(
  tool: string,
  input: Record<string, unknown>,
  projectRoot: string,
): Target | null {
  const file = typeof input.file_path === 'string' ? input.file_path : null
  if (!file) return null
  const root = projectRoot.endsWith('/') ? projectRoot : projectRoot + '/'
  if (!file.startsWith(root)) return null
  const rel = file.slice(root.length)
  const where = surfaceForPath(rel)
  if (!where) return null

  let text: string | null = null
  if (tool === 'Write' && typeof input.content === 'string') text = input.content
  if (tool === 'Edit' && typeof input.new_string === 'string') text = input.new_string
  if (text === null || !text.trim()) return null

  return {
    surface: where.surface,
    text,
    html: rel.endsWith('.html'),
    label: rel.split('/').pop() ?? rel,
    gated: where.gated,
  }
}

// A line in the text that tells the check to let a passage stand, with a reason:
// for the user's own verbatim lines or a quote from a client.
export const KEEP_MARKER = /voice-check:\s*keep\s+[a-z][\w-]*/i
