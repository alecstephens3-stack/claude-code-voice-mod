// The clock line: the day and time where the user is and in each zone their
// clients sit in, given to the model before every reply so it never works out
// "8 AM Central" in its head. Daylight saving comes from the time zone database,
// never a fixed offset. Pure functions; register.ts appends the line.

import { US_ZONES, type Zone } from './config'

type Parts = { weekday: string; day: string; month: string; time: string; hour24: number; abbr: string }

function parts(now: Date, tz: string): Parts {
  const p = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    weekday: 'long',
    day: 'numeric',
    month: 'short',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
    timeZoneName: 'short',
  }).formatToParts(now)
  const get = (t: string) => p.find(x => x.type === t)?.value ?? ''
  const hour24 = Number(new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', hourCycle: 'h23' }).format(now))
  return {
    weekday: get('weekday'),
    day: get('day'),
    month: get('month'),
    time: `${get('hour')}:${get('minute')} ${get('dayPeriod')}`,
    hour24,
    abbr: get('timeZoneName'),
  }
}

// A business day: weekdays, 8 AM to 5 PM local time.
export function isOpen(weekday: string, hour24: number): boolean {
  return weekday !== 'Saturday' && weekday !== 'Sunday' && hour24 >= 8 && hour24 < 17
}

// "open in Eastern and Central", "closed everywhere (weekend)", ...
export function hoursStatus(zones: { name: string; p: Parts }[]): string {
  const open = zones.filter(z => isOpen(z.p.weekday, z.p.hour24)).map(z => z.name)
  if (open.length === zones.length) return 'Client business hours: open in every zone'
  if (open.length) return `Client business hours: open in ${open.join(' and ')}, closed elsewhere`
  const weekend = zones.every(z => z.p.weekday === 'Saturday' || z.p.weekday === 'Sunday')
  return `Client business hours: closed everywhere${weekend ? ' (weekend)' : ''}`
}

export function clockLine(now: Date, home: Zone, clients: readonly Zone[] = US_ZONES): string {
  const h = parts(now, home.tz)
  const zones = clients.map(z => ({ name: z.name, p: parts(now, z.tz) }))
  const first = zones[0]?.p
  const list = zones.map(z => `${z.p.time} ${z.name} (${z.p.abbr})`).join(', ')
  const there = first ? ` For clients it is ${first.weekday} ${first.day} ${first.month}: ${list}. ${hoursStatus(zones)}.` : ''
  return (
    `Clock right now: ${h.weekday} ${h.day} ${h.month}, ${h.time} in ${home.name}.${there} ` +
    `Use these for any day or time you mention, and don't mention this note.`
  )
}
