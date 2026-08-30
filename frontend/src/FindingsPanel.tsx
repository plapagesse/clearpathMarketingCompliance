// The AI verdict, rendered the same way everywhere it appears.
//
// It started inside QueueView. The input view's detail section shows the same
// thing for the same submission, so it lives here instead of being written
// twice: one banner, one findings list, one history list, both views.

import './findings.css'

export type Finding = {
  id: string
  rule_id: string | null
  severity: string
  check_class: string
  summary: string
  explanation: string
  citation_url: string | null
  suggested_redline: string | null
}

export type HistoryEvent = { when: string; event: string }

const BANNER_TEXT: Record<string, string> = {
  quick_check: 'No issues found — quick double-check',
  needs_attention: 'Some issues flagged — read them before deciding',
  high_attention: 'Needs careful review',
}

type PanelProps = {
  attention?: string
  findingsCount?: number
  /** undefined while the detail request is still in flight. */
  findings?: Finding[]
}

export function FindingsPanel({ attention, findingsCount, findings }: PanelProps) {
  const bucket = attention || 'quick_check'
  const count = findingsCount ?? findings?.length ?? 0

  return (
    <>
      <div className={'banner banner-' + bucket}>
        {BANNER_TEXT[bucket]}
        <span className="banner-count">
          {' '}
          ({count} finding{count === 1 ? '' : 's'})
        </span>
      </div>

      {!findings && <p className="panel-note">Loading findings…</p>}

      {findings?.map((f) => (
        <div className="finding" key={f.id}>
          <div className="finding-head">
            <span className={'chip chip-' + f.severity}>{f.severity}</span>
            <span className="rule">{f.rule_id || f.check_class}</span>
          </div>
          <p className="finding-summary">{f.summary}</p>
          {f.suggested_redline && <p className="redline">Suggested: {f.suggested_redline}</p>}
          {f.citation_url && (
            <p className="panel-note">
              <a href={f.citation_url} target="_blank" rel="noreferrer">
                citation
              </a>
            </p>
          )}
        </div>
      ))}

      {findings?.length === 0 && <p className="panel-note">The engine raised no findings.</p>}
    </>
  )
}

export function HistoryList({ history }: { history?: HistoryEvent[] }) {
  if (!history) return <p className="panel-note">Loading history…</p>
  return (
    <ul className="history">
      {history.map((h, i) => (
        <li key={i}>
          <span className="history-when">{h.when.slice(0, 16).replace('T', ' ')}</span> {h.event}
        </li>
      ))}
    </ul>
  )
}
