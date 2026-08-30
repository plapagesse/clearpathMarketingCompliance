import { useEffect, useState, type FormEvent } from 'react'
import './App.css'
import './status.css'
import { AI_STATUSES, INPUT_TYPES, PRODUCTS } from './filters'
import { ageText, aiChip, humanChip, CHECKING_CHIP } from './format'
import SubmissionDetail, { type SubmissionCard } from './SubmissionDetail'

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
  const [aiStatus, setAiStatus] = useState('')
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
  // (a whole batch is in flight at once, so this is a set, not a single id).
  const [selected, setSelected] = useState<string[]>([])
  const [busyIds, setBusyIds] = useState<string[]>([])
  const [running, setRunning] = useState(false)
  const [batchNote, setBatchNote] = useState('')

  // The individual input — a conditional render, not a route. The view itself is
  // SubmissionDetail, the same component the Review Queue cycles through.
  const [openId, setOpenId] = useState('')

  const open = cards.find((c) => c.submission_id === openId) || null

  function load() {
    const params = new URLSearchParams()
    if (product) params.set('product', product)
    if (partner) params.set('partner', partner)
    if (inputType) params.set('input_type', inputType)
    if (aiStatus) params.set('ai_status', aiStatus)

    // A reload can hide a ticked card; a stale selection would then run the AI
    // on something the reviewer can no longer see. Every filter runs through
    // here, so no selector can leave Select-all pointing at hidden cards.
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

  useEffect(load, [product, partner, inputType, aiStatus])

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

  // What a run would actually POST: the ticked cards the engine has not already
  // looked at. Re-running a processed card would spend 15-40s to redraw the chip
  // it is already wearing (its detail view still offers a re-check). Derived once
  // here, so the button's count and runBatch's work list are the same list — the
  // label cannot promise a different run than the button performs.
  const checkedIds = new Set(
    cards.filter((c) => c.ai_status === 'processed').map((c) => c.submission_id),
  )
  const pendingIds = selected.filter((id) => !checkedIds.has(id))

  const batchLabel = running
    ? 'Running AI review…'
    : pendingIds.length === 0
      ? 'Nothing to check'
      : `Run AI review on ${pendingIds.length} unchecked`

  /** One POST per unchecked selected card, all fired at once. A failure is counted, not fatal. */
  async function runBatch() {
    const ids = pendingIds
    const skipped = selected.length - ids.length

    // The button is disabled in this state, so this is a guard rather than a
    // path the UI offers; it leaves the selection alone to be adjusted.
    if (ids.length === 0) {
      setBatchNote('All selected items already checked.')
      return
    }

    setRunning(true)
    setBatchNote('')
    // Every request starts now, so every card gets its chip now; each one clears
    // as its own response lands.
    setBusyIds(ids)
    let failures = 0

    await Promise.all(
      ids.map(async (id) => {
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
      }),
    )

    // "Checked 4 (skipped 2 already-checked, 1 failed)" — the parenthetical
    // carries only the parts that actually happened.
    const asides: string[] = []
    if (skipped) asides.push(`skipped ${skipped} already-checked`)
    if (failures) asides.push(`${failures} failed`)

    setBusyIds([])
    setRunning(false)
    setSelected([])
    setBatchNote(
      `Checked ${ids.length - failures}` + (asides.length ? ` (${asides.join(', ')})` : '') + '.',
    )
  }

  /** Fold one submission's fresh fields back into the grid behind the detail view. */
  function patchCard(id: string, fields: Partial<SubmissionCard>) {
    setCards((all) => all.map((c) => (c.submission_id === id ? { ...c, ...fields } : c)))
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
    return (
      <main className="page">
        <div className="topbar">
          <button className="back-button" onClick={() => setOpenId('')}>
            ← Back to inputs
          </button>
        </div>

        <SubmissionDetail
          key={open.submission_id}
          submissionId={open.submission_id}
          seed={open}
          onProcessed={(card) => patchCard(open.submission_id, aiFields(card))}
          // No auto-advance here: this flow is "open one input, look at it".
          // The detail refreshes itself; the grid behind it gets the new chip.
          onDecided={(decision) => patchCard(open.submission_id, { human_status: decision })}
        />
      </main>
    )
  }

  // --- the grid ------------------------------------------------------------ //

  return (
    <main className="page">
      <div className="topbar">
        {/* "Inputs", not "Review queue": the queue is its own tab now, and it is
            a cycle through these same submissions rather than this grid. */}
        <h1>Inputs</h1>

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
            {INPUT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>

        <label className="filter">
          AI status
          <select value={aiStatus} onChange={(e) => setAiStatus(e.target.value)}>
            <option value="">All statuses</option>
            {AI_STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
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
              <button
                className="run-button"
                onClick={runBatch}
                disabled={running || pendingIds.length === 0}
              >
                {batchLabel}
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
          const human = humanChip(card)
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
                <span className={human.className}>{human.label}</span>
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
