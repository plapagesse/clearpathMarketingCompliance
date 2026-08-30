import { useEffect, useState } from 'react'
import './queue.css'

type Finding = {
  id: string
  rule_id: string | null
  severity: string
  check_class: string
  summary: string
  explanation: string
  citation_url: string | null
  suggested_redline: string | null
}

type HistoryEvent = { when: string; event: string }

type Item = {
  submission_id: string
  product: string
  partner: string
  surface: string
  proposed_headline: string
  image_url: string | null
  sla_due: string | null
  days_left: number | null
  input_type: string
  ai_status: string
  max_severity?: string | null
  findings_count?: number
  attention?: string
  latest_check_run_id?: string
  findings?: Finding[]
  history?: HistoryEvent[]
}

const BANNER_TEXT: Record<string, string> = {
  quick_check: 'No issues found — quick double-check',
  needs_attention: 'Some issues flagged — read them before deciding',
  high_attention: 'Needs careful review',
}

function slaLine(item: Item) {
  if (item.sla_due === null) return 'No SLA date'
  if (item.days_left === null) return `SLA due ${item.sla_due}`
  if (item.days_left < 0) return `SLA due ${item.sla_due} — ${-item.days_left} day(s) overdue`
  if (item.days_left === 0) return `SLA due ${item.sla_due} — due today`
  return `SLA due ${item.sla_due} — ${item.days_left} day(s) left`
}

function QueueView() {
  const [products, setProducts] = useState<string[]>([])
  const [partners, setPartners] = useState<string[]>([])
  const [product, setProduct] = useState('')
  const [partner, setPartner] = useState('')

  const [items, setItems] = useState<Item[]>([])
  const [remaining, setRemaining] = useState(0)
  const [cursor, setCursor] = useState(0)
  const [reload, setReload] = useState(0)

  const [detail, setDetail] = useState<Item | null>(null)
  const [loading, setLoading] = useState(true)
  const [processing, setProcessing] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  const current = items[cursor] || null
  const currentId = current ? current.submission_id : ''

  useEffect(() => {
    fetch('/api/queue/filters')
      .then((r) => r.json())
      .then((d) => {
        setProducts(d.products || [])
        setPartners(d.partners || [])
      })
      .catch(() => setError('Could not load the filter lists.'))
  }, [])

  useEffect(() => {
    const params = new URLSearchParams()
    if (product) params.set('product', product)
    if (partner) params.set('partner', partner)
    setLoading(true)
    fetch('/api/queue?' + params.toString())
      .then((r) => r.json())
      .then((d) => {
        const list: Item[] = d.items || []
        setItems(list)
        setRemaining(d.remaining || 0)
        // Clamp rather than reset: after a decision the item at this index is
        // gone, so the same index is already the next one to review.
        setCursor((c) => (c < list.length ? c : 0))
      })
      .catch(() => setError('Could not load the queue.'))
      .then(() => setLoading(false))
  }, [product, partner, reload])

  useEffect(() => {
    if (!currentId) {
      setDetail(null)
      return
    }
    setDetail(null)
    setNote('')
    fetch('/api/queue/submission/' + currentId)
      .then((r) => r.json())
      .then((d) => setDetail(d))
      .catch(() => setError('Could not load ' + currentId + '.'))
  }, [currentId, reload])

  function runAiReview() {
    setProcessing(true)
    setError('')
    fetch('/api/queue/submission/' + currentId + '/process', { method: 'POST' })
      .then((r) => r.json().then((d) => ({ ok: r.ok, body: d })))
      .then(({ ok, body }) => {
        if (!ok) throw new Error(body.error || 'processing failed')
        setReload((n) => n + 1)
      })
      .catch((e) => setError('AI review failed: ' + e.message))
      .then(() => setProcessing(false))
  }

  function decide(decision: string) {
    setDeciding(true)
    setError('')
    fetch('/api/queue/submission/' + currentId + '/decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, note }),
    })
      .then((r) => r.json().then((d) => ({ ok: r.ok, body: d })))
      .then(({ ok, body }) => {
        if (!ok) throw new Error(body.error || 'decision failed')
        setReload((n) => n + 1)
      })
      .catch((e) => setError('Could not save the decision: ' + e.message))
      .then(() => setDeciding(false))
  }

  function skip() {
    if (items.length > 1) setCursor((c) => (c + 1) % items.length)
  }

  const shown = detail || current

  return (
    <div className="queue">
      <div className="queue-bar">
        <label>
          Product{' '}
          <select
            value={product}
            onChange={(e) => {
              // A filter change starts the reviewer back at the most urgent item.
              setCursor(0)
              setProduct(e.target.value)
            }}
          >
            <option value="">All products</option>
            {products.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label>
          Partner{' '}
          <select
            value={partner}
            onChange={(e) => {
              setCursor(0)
              setPartner(e.target.value)
            }}
          >
            <option value="">All partners</option>
            {partners.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <strong className="counter">{remaining} remaining to review</strong>
      </div>

      {error && <p className="error">{error}</p>}

      {loading && <p className="muted">Loading the queue…</p>}

      {!loading && !current && <p className="muted">Nothing left to review in this combo.</p>}

      {current && (
        <div className="review">
          <div className="shot-pane">
            {current.image_url ? (
              <img className="shot" src={current.image_url} alt={current.submission_id} />
            ) : (
              <p className="muted">No screenshot on file.</p>
            )}
          </div>

          <div className="side-pane">
            <div className="ident">
              <h2>{current.submission_id}</h2>
              <span className={'badge badge-' + current.input_type}>{current.input_type}</span>
            </div>
            <p className={current.days_left !== null && current.days_left < 0 ? 'sla overdue' : 'sla'}>
              {slaLine(current)}
            </p>
            <p className="muted small">
              {current.product} · {current.surface} · {current.partner}
            </p>

            {current.ai_status === 'unprocessed' ? (
              <div className="block">
                <p>Not yet processed</p>
                <button onClick={runAiReview} disabled={processing}>
                  {processing ? 'Processing…' : 'Run AI review'}
                </button>
                {processing && <p className="muted small">This takes 15–40 seconds.</p>}
              </div>
            ) : (
              <div className="block">
                <div className={'banner banner-' + (current.attention || 'quick_check')}>
                  {BANNER_TEXT[current.attention || 'quick_check']}
                  <span className="banner-count">
                    {' '}
                    ({current.findings_count} finding
                    {current.findings_count === 1 ? '' : 's'})
                  </span>
                </div>

                {!shown?.findings && <p className="muted small">Loading findings…</p>}

                {shown?.findings?.map((f) => (
                  <div className="finding" key={f.id}>
                    <div className="finding-head">
                      <span className={'chip chip-' + f.severity}>{f.severity}</span>
                      <span className="rule">{f.rule_id || f.check_class}</span>
                    </div>
                    <p className="finding-summary">{f.summary}</p>
                    {f.suggested_redline && (
                      <p className="redline">Suggested: {f.suggested_redline}</p>
                    )}
                    {f.citation_url && (
                      <p className="small">
                        <a href={f.citation_url} target="_blank" rel="noreferrer">
                          citation
                        </a>
                      </p>
                    )}
                  </div>
                ))}

                {shown?.findings?.length === 0 && (
                  <p className="muted small">The engine raised no findings.</p>
                )}
              </div>
            )}

            <div className="block">
              <h3>History</h3>
              {shown?.history ? (
                <ul className="history">
                  {shown.history.map((h, i) => (
                    <li key={i}>
                      <span className="muted">{h.when.slice(0, 16).replace('T', ' ')}</span>{' '}
                      {h.event}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">Loading history…</p>
              )}
            </div>

            <div className="block">
              <input
                className="note"
                type="text"
                placeholder="Note (optional)"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <div className="actions">
                <button className="approve" onClick={() => decide('approved')} disabled={deciding}>
                  Approve
                </button>
                <button className="reject" onClick={() => decide('rejected')} disabled={deciding}>
                  Reject
                </button>
                <button className="linkish" onClick={skip} disabled={items.length < 2}>
                  Next / Skip
                </button>
              </div>
              <p className="muted small">
                Item {cursor + 1} of {items.length}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default QueueView
