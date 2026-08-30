// The option lists behind the four selectors.
//
// The input grid and the Review Queue offer the same four filters — product,
// partner, input type, AI status — because they scope the same set of
// submissions; the grid shows it and the queue walks it. The options live here
// so "AI: issues" cannot come to mean one thing in one view and another in the
// other. Partner is absent on purpose: it is whatever partners the data
// actually contains, fetched from /api/queue/filters.

export type Option = { value: string; label: string }

/** The closed product set the engine has rulebooks for. */
export const PRODUCTS: Option[] = [
  { value: 'personal_loan', label: 'Personal loan' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'mortgage_prequal', label: 'Mortgage prequal' },
]

export const INPUT_TYPES: Option[] = [
  { value: 'proposed', label: 'Proposed' },
  { value: 'production', label: 'Production' },
]

// Values are the server's closed vocabulary; labels repeat the chip wording, so
// filtering by "AI: issues" picks out exactly the cards wearing that chip.
export const AI_STATUSES: Option[] = [
  { value: 'not_checked', label: 'Not checked' },
  { value: 'clean', label: 'AI: clean' },
  { value: 'review', label: 'AI: review' },
  { value: 'issues', label: 'AI: issues' },
]
