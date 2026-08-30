// Tiny display helpers shared by the input grid and the queue.
//
// Both views show how long a submission has been waiting, what the AI made of
// it, and whether a person has signed off. Those sentences live here so the two
// views can never word them differently — a card in the grid and the same
// submission in the detail view read identically.

export type AiSummary = {
  ai_status?: string
  attention?: string
  findings_count?: number
}

export type HumanSummary = {
  human_status?: string
}

/** "added 3d ago" from the server's days_ago. */
export function ageText(daysAgo: number | null | undefined): string {
  if (daysAgo === null || daysAgo === undefined) return 'no submission date'
  if (daysAgo <= 0) return 'added today'
  if (daysAgo === 1) return 'added 1d ago'
  return `added ${daysAgo}d ago`
}

export type Chip = { label: string; className: string }

const ATTENTION_CHIPS: Record<string, Chip> = {
  quick_check: { label: 'AI: clean', className: 'ai-chip ai-chip-clean' },
  needs_attention: { label: 'AI: review', className: 'ai-chip ai-chip-review' },
  high_attention: { label: 'AI: issues', className: 'ai-chip ai-chip-issues' },
}

/** The status chip: grey until the engine has run, then coloured by worst finding. */
export function aiChip(item: AiSummary | null | undefined): Chip {
  if (!item || item.ai_status !== 'processed') {
    return { label: 'Not checked', className: 'ai-chip ai-chip-none' }
  }
  return ATTENTION_CHIPS[item.attention || 'quick_check'] || ATTENTION_CHIPS.quick_check
}

/** Shown on a card while its own AI review is in flight. */
export const CHECKING_CHIP: Chip = { label: 'Checking…', className: 'ai-chip ai-chip-busy' }

const HUMAN_CHIPS: Record<string, Chip> = {
  approved: { label: 'Human: approved', className: 'human-chip human-chip-approved' },
  rejected: { label: 'Human: rejected', className: 'human-chip human-chip-rejected' },
}

/** The companion to aiChip: has a person signed this off, and which way?
 *
 * Grey em-dash until someone decides, then green or red. The server always
 * sends human_status, so an undecided submission is a state here, not a gap. */
export function humanChip(item: HumanSummary | null | undefined): Chip {
  return (
    HUMAN_CHIPS[item?.human_status || 'none'] || {
      label: 'Human: —',
      className: 'human-chip human-chip-none',
    }
  )
}
