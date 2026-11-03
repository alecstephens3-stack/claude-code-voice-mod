// The schedule guard: a reply that says something about the user's schedule or inbox
// ("needs a new date", "hasn't replied", "nothing came in") must come after a look at
// a calendar or an inbox in the same turn. Pure functions; register.ts tracks the
// lookups and appends the note.
//
// I added this after the model said four times in one week that a meeting or a reply
// was missing when it was sitting in the inbox or on the calendar. A line in memory
// telling it to check first did not hold. This check does.

// Claims that a meeting, date, invite or reply is missing, moved or open. Kept narrow:
// it fires on what a missed lookup sounds like, not on every mention of a meeting.
const CLAIMS: RegExp[] = [
  /\bneeds? (?:a )?new (?:date|time)\b/i,
  /\b(?:new )?date (?:is )?(?:still )?open\b/i,
  /\bdidn['’]?t happen\b/i,
  /\b(?:has|have)n['’]?t (?:been )?(?:re)?(?:scheduled|booked|rebooked)\b/i,
  /\bnot (?:yet )?(?:re)?(?:scheduled|booked|rebooked)\b/i,
  /\bno (?:new )?(?:date|invite|invitation|meeting link|reply|replies|answer) (?:yet|so far)\b/i,
  /\bnothing (?:new )?(?:has )?(?:come|came) in\b/i,
  /\b(?:has|have)n['’]?t (?:replied|answered|sent|accepted|responded)\b/i,
  /\bisn['’]?t on (?:your|the|his|her) calendar\b/i,
  /\bnot on (?:your|the|his|her) calendar\b/i,
]

export function scheduleClaim(answer: string): string | null {
  for (const re of CLAIMS) {
    const m = answer.match(re)
    if (m) return m[0]
  }
  return null
}

// Tool calls that count as a look: a calendar or inbox connector, Outlook, or the
// gws (Google Workspace) CLI on calendar or Gmail.
export function isScheduleLookup(tool: string, input: Record<string, unknown>): boolean {
  if (/Google_Calendar|Gmail|Microsoft_365__outlook/i.test(tool)) return true
  if (tool === 'Bash') {
    const cmd = String(input.command ?? '')
    return /\bgws\s+(?:calendar|gmail)\b/.test(cmd)
  }
  return false
}

export function scheduleNote(phrase: string): string {
  return (
    `Schedule check: your last reply said "${phrase}" without a calendar or inbox lookup in that turn. ` +
    'Search the calendar and the inbox now (every account you can reach), ' +
    'and correct the reply if they show otherwise. Do not mention this note.'
  )
}
