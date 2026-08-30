import { useEffect, useState, type FormEvent } from 'react'
import './App.css'
import './status.css'
import { FindingsPanel, HistoryList, type Finding, type HistoryEvent } from './FindingsPanel'
import { ageText, aiChip, CHECKING_CHIP } from './format'

type SubmissionCard = {
  submission_id: string
  product: string
  partner: string
  surface: string
  mode: string
  date_submitted: string | null
  days_ago: number | null
  sla_due: string | null
  image_url: string | null
  input_type: string
  ai_status: string
  attention?: string
  max_severity?: string | null
  findings_count?: number
}

/** GET /api/queue/submission/<id> — the queue's detail payload, reused verbatim. */
type Detail = SubmissionCard & {
  proposed_headline?: string
  findings?: Finding[]
  history?: HistoryEvent[]
}

const PRODUCTS = [
  { value: 'personal_loan', label: 'Personal loan' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'mortgage_prequal', label: 'Mortgage prequal' },
]

// Batch reviews in flight at once — bounded to respect API rate limits and keep failures isolated.
const BATCH_CONCURRENCY = 4

/** The AI fields a /process response carries back onto the card it belongs to. */
function aiFields(item: SubmissionCard) {
  return {
    ai_status: item.ai_status,
    attention: item.attention,
    max_severity: item.max_severity,
    findings_count: item.findings_count,
  }
}

function InputView() {
  const [product, setProduct] = useState('')
  const [partner, setPartner] = useState('')
  const [inputType, setInputType] = useState('')
  const [cards, setCards] = useState<SubmissionCard[]>([])
  const [partners, setPartners] = useState<string[]>([])
  const [loadError, setLoadError] = useState('')

  const [showForm, setShowForm] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [formProduct, setFormProduct] = useState('personal_loan')
  const [formPartner, setFormPartner] = useState('')
  const [formInputType, setFormInputType] = useState('proposed')
  const [formNotes, setFormNotes] = useState('')
  const [formError, setFormError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Batch AI review: which cards are ticked, which ones are being checked now
  // (several at a time — see BATCH_CONCURRENCY).
  const [selected, setSelected] = useState<string[]>([])
  const [busyIds, setBusyIds] = useState<string[]>([])
  const [running, setRunning] = useState(false)
  const [batchNote, setBatchNote] = useState('')

  // Individual input view — a conditional render, not a route.
  const [openId, setOpenId] = useState('')
  const [detail, setDetail] = useState<Detail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [processing, setProcessing] = useState(false)

  const open = cards.find((c) => c.submission_id === openId) || null

  function load() {
    const params = new URLSearchParams()
    if (product) params.set('product', product)
    if (partner) params.set('partner', partner)
    if (inputType) params.set('input_type', inputType)

    // A reload can hide a ticked card; a stale selection would then run the AI
    // on something the reviewer can no longer see.
    setSelected([])
    setBatchNote('')
    setLoadError('')
    fetch('/api/review/submissions?' + params.toString())
      .then((r) => r.json())
      .then(setCards)
      .catch(() => setLoadError('Could not load submissions.'))

    // Unfiltered fetch just to keep the partner dropdown complete.
    fetch('/api/review/submissions')
      .then((r) => r.json())
      .then((all: SubmissionCard[]) =>
        setPartners(Array.from(new Set(all.map((s) => s.partner))).sort()),
      )
      .catch(() => setPartners([]))
  }

  useEffect(load, [product, partner, inputType])

  useEffect(() => {
    if (!openId) {
      setDetail(null)
      return
    }
    setDetail(null)
    setDetailError('')
    fetch('/api/queue/submission/' + openId)
      .then((r) => r.json())
      .then(setDetail)
      .catch(() => setDetailError('Could not load ' + openId + '.'))
  }, [openId])

  function toggleSelected(id: string) {
    setBatchNote('')
    setSelected((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : ids.concat(id)))
  }

  // "All" means every card the current filter is showing, not every row in the DB.
  const allSelected = cards.length > 0 && cards.every((c) => selected.includes(c.submission_id))

  function toggleSelectAll() {
    setBatchNote('')
    setSelected(allSelected ? [] : cards.map((c) => c.submission_id))
  }

  /** One POST per selected card, BATCH_CONCURRENCY at a time. A failure is counted, not fatal. */
  async function runBatch() {
    const ids = selected
    setRunning(true)
    setBatchNote('')
    setBusyIds([])
    let failures = 0
    let next = 0

    // A worker pulls the next id off the shared queue and waits only on its own
    // request, so one slow (or failing) submission never blocks the others.
    async function worker() {
      while (next < ids.length) {
        const id = ids[next++]
        setBusyIds((busy) => busy.concat(id))
        try {
          const response = await fetch('/api/queue/submission/' + id + '/process', {
            method: 'POST',
          })
          const body = await response.json()
          if (!response.ok) throw new Error(body.error || 'processing failed')
          setCards((all) =>
            all.map((c) => (c.submission_id === id ? { ...c, ...aiFields(body) } : c)),
          )
        } catch {
          failures += 1
        }
        setBusyIds((busy) => busy.filter((x) => x !== id))
      }
    }

    await Promise.all(Array.from({ length: Math.min(BATCH_CONCURRENCY, ids.length) }, worker))

    setBusyIds([])
    setRunning(false)
    setSelected([])
    setBatchNote(
      failures === 0
        ? `Checked ${ids.length} submission${ids.length === 1 ? '' : 's'}.`
        : `Checked ${ids.length - failures} of ${ids.length} — ${failures} failed.`,
    )
  }

  /** Run the AI on the one submission the detail section is showing. */
  function processOpen() {
    setProcessing(true)
    setDetailError('')
    fetch('/api/queue/submission/' + openId + '/process', { method: 'POST' })
      .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
      .then(({ ok, body }) => {
        if (!ok) throw new Error(body.error || 'processing failed')
        setCards((all) =>
          all.map((c) => (c.submission_id === openId ? { ...c, ...aiFields(body) } : c)),
        )
        return fetch('/api/queue/submission/' + openId)
          .then((r) => r.json())
          .then(setDetail)
      })
      .catch((e: Error) => setDetailError('AI review failed: ' + e.message))
      .then(() => setProcessing(false))
  }

  function openForm() {
    setFormProduct(product || 'personal_loan')
    setFormPartner(partner)
    setFormInputType(inputType === 'production' ? 'production' : 'proposed')
    setFormNotes('')
    setFormError('')
    setFile(null)
    setShowForm(true)
  }

  function submitForm(event: FormEvent) {
    event.preventDefault()
    if (!file) {
      setFormError('Choose an image file.')
      return
    }
    const body = new FormData()
    body.append('file', file)
    body.append('product', formProduct)
    body.append('partner', formPartner)
    body.append('input_type', formInputType)
    body.append('notes', formNotes)

    setSubmitting(true)
    setFormError('')
    fetch('/api/review/submissions', { method: 'POST', body })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data })))
      .then((result) => {
        if (!result.ok) throw new Error(result.data.error || 'Upload failed.')
        setShowForm(false)
        load()
      })
      .catch((err: Error) => setFormError(err.message))
      .then(() => setSubmitting(false))
  }

  // --- individual input ---------------------------------------------------- //

  if (open) {
    const shown: Detail = { ...open, ...(detail || {}) }
    const chip = aiChip(shown)
    return (
      <main className="page">
        <div className="topbar">
          <button className="back-button" onClick={() => setOpenId('')}>
            ← Back to inputs
          </button>
        </div>

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
              <span className={chip.className}>{chip.label}</span>
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

            {detailError && <p className="error">{detailError}</p>}

            <div className="detail-block">
              <h2>AI review</h2>
              {shown.ai_status === 'unprocessed' ? (
                <>
                  <p className="empty">This input has not been checked yet.</p>
                  <button className="run-button" onClick={processOpen} disabled={processing}>
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
          </div>
        </div>
      </main>
    )
  }

  // --- the grid ------------------------------------------------------------ //

  return (
    <main className="page">
      <div className="topbar">
        <h1>Review queue</h1>

        <label className="filter">
          Product
          <select value={product} onChange={(e) => setProduct(e.target.value)}>
            <option value="">All products</option>
            {PRODUCTS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <label className="filter">
          Partner
          <select value={partner} onChange={(e) => setPartner(e.target.value)}>
            <option value="">All partners</option>
            {partners.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>

        <label className="filter">
          Input type
          <select value={inputType} onChange={(e) => setInputType(e.target.value)}>
            <option value="">All input types</option>
            <option value="proposed">Proposed</option>
            <option value="production">Production</option>
          </select>
        </label>

        <button className="add-button" onClick={() => (showForm ? setShowForm(false) : openForm())}>
          {showForm ? '×' : '+'}
        </button>
      </div>

      {showForm && (
        <div className="upload-form">
          <h2>New submission</h2>
          <form onSubmit={submitForm}>
            <label className="field">
              Screenshot
              <input
                type="file"
                accept="image/*"
                onChange={(e) => setFile(e.target.files ? e.target.files[0] : null)}
              />
            </label>

            <label className="field">
              Product
              <select value={formProduct} onChange={(e) => setFormProduct(e.target.value)}>
                {PRODUCTS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              Partner
              <input
                type="text"
                value={formPartner}
                placeholder="credit_karma"
                onChange={(e) => setFormPartner(e.target.value)}
              />
            </label>

            <label className="field">
              Input type
              <select value={formInputType} onChange={(e) => setFormInputType(e.target.value)}>
                <option value="proposed">Proposed</option>
                <option value="production">Production</option>
              </select>
            </label>

            <label className="field">
              Notes
              <textarea
                rows={3}
                value={formNotes}
                onChange={(e) => setFormNotes(e.target.value)}
              />
            </label>

            {formError && <p className="error">{formError}</p>}

            <button type="submit" disabled={submitting}>
              {submitting ? 'Uploading…' : 'Submit'}
            </button>
          </form>
        </div>
      )}

      {cards.length > 0 && (
        <div className="batch-bar">
          <button className="linkish" onClick={toggleSelectAll} disabled={running}>
            {allSelected ? 'Deselect all' : 'Select all'}
          </button>
          {selected.length > 0 && (
            <>
              <button className="run-button" onClick={runBatch} disabled={running}>
                {running ? 'Running AI review…' : `Run AI review on ${selected.length} selected`}
              </button>
              <button className="linkish" onClick={() => setSelected([])} disabled={running}>
                Clear selection
              </button>
            </>
          )}
        </div>
      )}

      {batchNote && <p className="batch-note">{batchNote}</p>}

      {loadError && <p className="error">{loadError}</p>}
      {!loadError && cards.length === 0 && <p className="empty">No submissions match this filter.</p>}

      <div className="grid">
        {cards.map((card) => {
          const chip = busyIds.includes(card.submission_id) ? CHECKING_CHIP : aiChip(card)
          return (
            <div
              className="card"
              key={card.submission_id}
              onClick={() => setOpenId(card.submission_id)}
            >
              <input
                className="card-check"
                type="checkbox"
                aria-label={'Select ' + card.submission_id}
                checked={selected.includes(card.submission_id)}
                onClick={(e) => e.stopPropagation()}
                onChange={() => toggleSelected(card.submission_id)}
              />
              {card.image_url ? (
                <img className="thumb" src={card.image_url} alt={card.submission_id} />
              ) : (
                <div className="thumb thumb-missing">no screenshot</div>
              )}
              <div className="card-id">{card.submission_id}</div>
              <div className="card-chips">
                <span className={'badge badge-' + card.input_type}>
                  {card.input_type === 'production' ? 'Production' : 'Proposed'}
                </span>
                <span className={chip.className}>{chip.label}</span>
              </div>
              <div className="card-surface">{card.surface}</div>
              <div className="sla">{ageText(card.days_ago)}</div>
            </div>
          )
        })}
      </div>
    </main>
  )
}

export default InputView
