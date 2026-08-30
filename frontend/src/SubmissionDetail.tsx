// One submission, looked at properly — the only place a reviewer ever decides.
//
// Both entry points render this same component: opening a card in the input
// grid, and the Review Queue, which is now nothing but this view plus a cycle
// through the undecided set. The queue used to own a second, near-identical
// layout of its own; that is gone, so a submission cannot look or behave one
// way in the grid and another in the queue.
//
// The component fetches its own detail, runs the AI, and records the decision.
// What happens *after* a decision is the caller's business: the queue advances
// to the next item, the grid just lets the refreshed detail stand.

import { useEffect, useState } from 'react'
import './App.css'
import './status.css'
import { FindingsPanel, HistoryList, type Finding, type HistoryEvent } from './FindingsPanel'
import { ageText, aiChip, humanChip } from './format'

/** A row as the two list endpoints serialize it, and the base of the detail payload. */
export type SubmissionCard = {
  submission_id: string
  product: string
  partner: string
  surface: string
  date_submitted: string | null
  days_ago: number | null
  sla_due: string | null
  image_url: string | null
  input_type: string
  ai_status: string
  attention?: string
  max_severity?: string | null
  findings_count?: number
  proposed_headline?: string | null
  human_status?: string
  decided_by?: string | null
  decided_at?: string | null
}

/** GET /api/queue/submission/<id> — the card, plus the latest run and the history. */
export type SubmissionDetailData = SubmissionCard & {
  findings?: Finding[]
  history?: HistoryEvent[]
}

type Props = {
  submissionId: string
  /** The list row already in hand, shown while the detail request is in flight. */
  seed?: SubmissionCard | null
  /** The refreshed card after an AI run, so a parent list can restyle its chip. */
  onProcessed?: (card: SubmissionCard) => void
  /** Fired once a decision is saved. The queue advances on this; the grid ignores it. */
  onDecided?: (decision: string) => void
  /** Supplied by the queue only, and only when there is somewhere to skip to. */
  onSkip?: () => void
}

function SubmissionDetail({ submissionId, seed, onProcessed, onDecided, onSkip }: Props) {
  const [detail, setDetail] = useState<SubmissionDetailData | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [processing, setProcessing] = useState(false)
  const [deciding, setDeciding] = useState(false)
  // Bumped by anything that changes the submission server-side, to refetch it.
  const [reload, setReload] = useState(0)

  // One mount per submission — callers key this component on the id, so a new
  // submission arrives as a fresh instance rather than as a prop change this
  // has to unpick. Nothing here needs to reset the note or the findings.
  useEffect(() => {
    let ignore = false
    fetch('/api/queue/submission/' + submissionId)
      .then((r) => r.json())
      .then((d: SubmissionDetailData) => {
        if (!ignore) setDetail(d)
      })
      .catch(() => {
        if (!ignore) setError('Could not load ' + submissionId + '.')
      })
    // The reviewer may well have moved on by the time this lands; a late
    // response must not overwrite the submission now on screen.
    return () => {
      ignore = true
    }
  }, [submissionId, reload])

  /** Run the real pipeline on this submission. 15-40s, so the button says so. */
  function runAiReview() {
    setProcessing(true)
    setError('')
    fetch('/api/queue/submission/' + submissionId + '/process', { method: 'POST' })
      .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
      .then(({ ok, body }) => {
        if (!ok) throw new Error(body.error || 'processing failed')
        onProcessed?.(body)
        setReload((n) => n + 1)
      })
      .catch((e: Error) => setError('AI review failed: ' + e.message))
      .then(() => setProcessing(false))
  }

  function decide(decision: string) {
    setDeciding(true)
    setError('')
    fetch('/api/queue/submission/' + submissionId + '/decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, note }),
    })
      .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
      .then(({ ok, body }) => {
        if (!ok) throw new Error(body.error || 'decision failed')
        setNote('')
        // Refresh in place: the chip and the history now say something new. If
        // the caller moves us to another submission, the effect above discards
        // this fetch rather than writing it over the new one.
        setReload((n) => n + 1)
        onDecided?.(decision)
      })
      .catch((e: Error) => setError('Could not save the decision: ' + e.message))
      .then(() => setDeciding(false))
  }

  // Either one alone is a complete card, so the page draws in full as soon as we
  // have either; merging lets the detail response supersede the seed field by
  // field. Without a seed — a caller that only knows the id — we wait.
  const base = detail || seed
  if (!base) return <p className="empty">Loading…</p>
  const shown: SubmissionDetailData = { ...base, ...detail }

  const ai = aiChip(shown)
  const human = humanChip(shown)

  return (
    <div className="detail">
      <div className="detail-shot">
        {shown.image_url ? (
          <img src={shown.image_url} alt={shown.submission_id} />
        ) : (
          <p className="empty">No screenshot on file.</p>
        )}
      </div>

      <div className="detail-side">
        <div className="detail-head">
          <h1>{shown.submission_id}</h1>
          <span className={'badge badge-' + shown.input_type}>
            {shown.input_type === 'production' ? 'Production' : 'Proposed'}
          </span>
          <span className={ai.className}>{ai.label}</span>
          <span className={human.className}>{human.label}</span>
        </div>
        <p className="sla">{ageText(shown.days_ago)}</p>

        <dl className="meta">
          <dt>Product</dt>
          <dd>{shown.product}</dd>
          <dt>Partner</dt>
          <dd>{shown.partner}</dd>
          <dt>Surface</dt>
          <dd>{shown.surface}</dd>
          <dt>Submitted</dt>
          <dd>{shown.date_submitted || '—'}</dd>
          <dt>SLA due</dt>
          <dd>{shown.sla_due || '—'}</dd>
          {shown.proposed_headline && (
            <>
              <dt>Headline</dt>
              <dd>{shown.proposed_headline}</dd>
            </>
          )}
        </dl>

        {error && <p className="error">{error}</p>}

        <div className="detail-block">
          <h2>AI review</h2>
          {shown.ai_status === 'unprocessed' ? (
            <>
              <p className="empty">This input has not been checked yet.</p>
              <button className="run-button" onClick={runAiReview} disabled={processing}>
                {processing ? 'Processing…' : 'Run AI review'}
              </button>
              {processing && <p className="empty">This takes 15–40 seconds.</p>}
            </>
          ) : (
            <FindingsPanel
              attention={shown.attention}
              findingsCount={shown.findings_count}
              findings={detail?.findings}
            />
          )}
        </div>

        <div className="detail-block">
          <h2>History</h2>
          <HistoryList history={detail?.history} />
        </div>

        <div className="detail-block">
          <h2>Decision</h2>
          <input
            className="note"
            type="text"
            placeholder="Note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="decide-actions">
            <button className="approve" onClick={() => decide('approved')} disabled={deciding}>
              Approve
            </button>
            <button className="reject" onClick={() => decide('rejected')} disabled={deciding}>
              Reject
            </button>
            {onSkip && (
              <button className="linkish" onClick={onSkip} disabled={deciding}>
                Next / Skip
              </button>
            )}
          </div>
          {shown.human_status && shown.human_status !== 'none' && (
            <p className="empty">
              {shown.human_status} by {shown.decided_by || 'reviewer'}
              {shown.decided_at ? ' · ' + shown.decided_at.slice(0, 16).replace('T', ' ') : ''}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

export default SubmissionDetail
