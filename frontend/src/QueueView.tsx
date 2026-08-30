import { useEffect, useState } from 'react'
import './queue.css'
import './status.css'
import { FindingsPanel, HistoryList, type Finding, type HistoryEvent } from './FindingsPanel'
import { ageText, aiChip } from './format'

type Item = {
  submission_id: string
  product: string
  partner: string
  surface: string
  proposed_headline: string
  image_url: string | null
  date_submitted: string | null
  days_ago: number | null
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

function QueueView() {
  const [products, setProducts] = useState<string[]>([])
  const [partners, setPartners] = useState<string[]>([])
  const [product, setProduct] = useState('')
  const [partner, setPartner] = useState('')
  const [inputType, setInputType] = useState('')

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
    if (inputType) params.set('input_type', inputType)
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
  }, [product, partner, inputType, reload])

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
  const chip = current ? aiChip(current) : null

  return (
    <div className="queue">
      <div className="queue-bar">
        <label>
          Product{' '}
          <select
            value={product}
            onChange={(e) => {
              // A filter change starts the reviewer back at the oldest item.
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
        <label>
          Input type{' '}
          <select
            value={inputType}
            onChange={(e) => {
              setCursor(0)
              setInputType(e.target.value)
            }}
          >
            <option value="">All input types</option>
            <option value="proposed">Proposed</option>
            <option value="production">Production</option>
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
              {chip && <span className={chip.className}>{chip.label}</span>}
            </div>
            <p className="age">{ageText(current.days_ago)}</p>
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
                <FindingsPanel
                  attention={current.attention}
                  findingsCount={current.findings_count}
                  findings={shown?.findings}
                />
              </div>
            )}

            <div className="block">
              <h3>History</h3>
              <HistoryList history={shown?.history} />
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
