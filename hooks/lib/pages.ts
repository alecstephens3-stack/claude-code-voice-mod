// Checklist pages: fill the page template (pages/checklist-template.html) with
// the model's steps. Pure functions.

// copy: text the user pastes somewhere (a form box, an email). Shown in its own box with a Copy button.
export type Step = { text: string; detail?: string; copy?: string }

export const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,80}$/

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function slug(title: string): string {
  return (
    title
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60)
      .replace(/-+$/g, '') || 'checklist'
  )
}

// "2026-10-03" and "3 Oct 2026" from a Date, in the user's time zone.
export function dates(now: Date, tz = 'UTC'): { iso: string; human: string } {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: tz,
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).formatToParts(now)
  const get = (t: string) => parts.find(p => p.type === t)?.value ?? ''
  const month = new Intl.DateTimeFormat('en-US', { timeZone: tz, month: '2-digit' }).format(now)
  return { iso: `${get('year')}-${month}-${get('day').padStart(2, '0')}`, human: `${get('day')} ${get('month')} ${get('year')}` }
}

export function checklistId(title: string, now: Date, given?: string, tz = 'UTC'): string {
  if (given && ID_PATTERN.test(given)) return given
  return `${slug(title)}-${dates(now, tz).iso}`.slice(0, 81).replace(/-+$/g, '')
}

// The step text without a leading "1." or "- [ ]" the model may have kept.
export function cleanStep(text: string): string {
  return text.replace(/^\s*(?:[-*]\s*)?(?:\[[ xX]\]\s*)?(?:\d+[.)]\s*)?/, '').trim()
}

export function itemsHtml(steps: readonly Step[]): string {
  return steps
    .map((s, i) => {
      const id = `s${i + 1}`
      // The detail sits inside the label so the whole step is one hover area and one click target.
      const detail = s.detail?.trim() ? `<span class="ck-detail">${escapeHtml(s.detail.trim())}</span>` : ''
      // The paste box sits outside the label, so pressing Copy never ticks the step.
      const copy = s.copy?.trim()
        ? `<div class="ck-paste"><pre class="ck-paste-text">${escapeHtml(s.copy.trim())}</pre><button type="button" class="sai-copy ck-paste-btn">Copy</button></div>`
        : ''
      return `<li class="ck-item" data-id="${id}"><label><input type="checkbox" id="${id}"><span class="ck-text">${escapeHtml(cleanStep(s.text))}</span>${detail}</label>${copy}</li>`
    })
    .join('\n')
}

export function renderChecklist(
  template: string,
  args: { title: string; intro?: string; steps: readonly Step[]; id: string; created: string },
): string {
  const fill: Record<string, string> = {
    '{{TITLE}}': escapeHtml(args.title.trim()),
    '{{INTRO}}': escapeHtml((args.intro ?? '').trim()),
    '{{CHECKLIST_ID}}': args.id,
    '{{CREATED}}': escapeHtml(args.created),
    '{{ITEMS}}': itemsHtml(args.steps),
  }
  let out = template
  for (const [key, value] of Object.entries(fill)) out = out.split(key).join(value)
  return out
}

// The ticks as plain lines the model can read back.
export function ticksAsText(
  saved: { title?: string; items?: { id: string; text: string }[]; ticks?: Record<string, boolean>; updated?: string | null },
): string {
  const items = saved.items ?? []
  const ticks = saved.ticks ?? {}
  if (!items.length) {
    const done = Object.entries(ticks).filter(([, v]) => v).map(([k]) => k)
    return done.length ? `Ticked: ${done.join(', ')}` : 'Nothing ticked yet.'
  }
  const lines = items.map((it, i) => `${ticks[it.id] ? '[x]' : '[ ]'} ${i + 1}. ${it.text}`)
  const doneCount = items.filter(it => ticks[it.id]).length
  return [`${saved.title ?? 'Checklist'}: ${doneCount} of ${items.length} done${saved.updated ? ` (last change ${saved.updated})` : ''}`, ...lines].join('\n')
}
