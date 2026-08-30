// Tiny display helpers shared by the input grid and the queue.
//
// Both views show how long a submission has been waiting and what the AI made
// of it. Those two sentences live here so the two views can never word them
// differently — a card in the grid and the same submission in the queue read
// identically.

export type AiSummary = {
  ai_status?: string
  attention?: string
  findings_count?: number
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
