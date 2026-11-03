// Per-project settings. The mod is opt-in: it only acts in a project that has
// .claude/voice-mod.json (an empty `{}` is enough). Everything in it is optional.
// Pure functions; register.ts reads the file.

export const CONFIG_FILE = '.claude/voice-mod.json'

export type Zone = { name: string; tz: string }

export type Config = {
  // Command that opens a URL. macOS `open` by default; `["xdg-open"]` on Linux.
  browser: string[]
  // Base URL of a server that implements the checklist API (server/checklist_server.py).
  // When it does not answer, pages open as files and ticks stay in the browser.
  pageServer: string
  // Where checklist pages are written, relative to the project root.
  pagesDir: string
  // Where the score log and the loaded-sessions file go, relative to the project root.
  dataDir: string
  // The writing check. Relative paths resolve against the plugin root.
  checker: string
  // Files a number in outgoing copy must appear in (relative to the project root).
  proofFiles: string[]
  // A regex the HTML of open_page must match, e.g. your design system's class
  // prefix. Empty means no check.
  designFingerprint: string
  // The user's own clock, and the zones their clients are in.
  home: Zone
  clientZones: Zone[]
}

export const US_ZONES: Zone[] = [
  { name: 'Eastern', tz: 'America/New_York' },
  { name: 'Central', tz: 'America/Chicago' },
  { name: 'Mountain', tz: 'America/Denver' },
  { name: 'Pacific', tz: 'America/Los_Angeles' },
]

export function defaults(localTz = 'UTC'): Config {
  return {
    browser: ['open'],
    pageServer: 'http://localhost:7777',
    pagesDir: 'artifacts/checklists',
    dataDir: '.voice-mod',
    checker: 'checker/voice_check.py',
    proofFiles: [],
    designFingerprint: '',
    home: { name: 'your time zone', tz: localTz },
    clientZones: US_ZONES,
  }
}

const isZone = (z: unknown): z is Zone =>
  !!z && typeof (z as Zone).name === 'string' && typeof (z as Zone).tz === 'string'

const strings = (v: unknown): string[] | null =>
  Array.isArray(v) && v.length && v.every(x => typeof x === 'string') ? (v as string[]) : null

const relative = (v: unknown): string | null =>
  typeof v === 'string' && v.trim() && !v.includes('..') ? v.trim().replace(/^\.\//, '').replace(/\/+$/, '') : null

// Merge the file's JSON over the defaults. Unknown keys and wrong types are ignored,
// so a typo never stops the mod; it falls back to the default for that key.
export function parseConfig(raw: string, localTz = 'UTC'): Config {
  const c = defaults(localTz)
  let j: Record<string, unknown> = {}
  try {
    const parsed = JSON.parse(raw || '{}')
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) j = parsed
  } catch {
    return c
  }
  const browser = strings(j.browser)
  if (browser) c.browser = browser
  if (typeof j.pageServer === 'string' && /^https?:\/\/[^\s]+$/.test(j.pageServer)) c.pageServer = j.pageServer.replace(/\/+$/, '')
  c.pagesDir = relative(j.pagesDir) ?? c.pagesDir
  c.dataDir = relative(j.dataDir) ?? c.dataDir
  if (typeof j.checker === 'string' && j.checker.trim()) c.checker = j.checker.trim()
  const proof = strings(j.proofFiles)
  if (proof) c.proofFiles = proof.filter(p => !p.includes('..'))
  if (typeof j.designFingerprint === 'string') {
    try {
      new RegExp(j.designFingerprint)
      c.designFingerprint = j.designFingerprint
    } catch {}
  }
  if (isZone(j.home)) c.home = j.home
  if (Array.isArray(j.clientZones) && j.clientZones.length && j.clientZones.every(isZone)) c.clientZones = j.clientZones
  return c
}
