import { useEffect, useState, type FormEvent } from 'react'
import './App.css'

type SubmissionCard = {
  submission_id: string
  product: string
  partner: string
  surface: string
  mode: string
  date_submitted: string | null
  sla_due: string | null
  image_url: string | null
  input_type: string
}

const PRODUCTS = [
  { value: 'personal_loan', label: 'Personal loan' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'mortgage_prequal', label: 'Mortgage prequal' },
]

// Whole days from today to the SLA date. Negative means overdue.
function daysUntil(isoDate: string | null) {
  if (!isoDate) return null
  const due = new Date(isoDate + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((due.getTime() - today.getTime()) / 86400000)
}

function slaText(days: number | null) {
  if (days === null) return 'no SLA date'
  if (days > 0) return `due in ${days}d`
  if (days === 0) return 'due today'
  return `OVERDUE by ${-days}d`
}

function InputView() {
  const [product, setProduct] = useState('')
  const [partner, setPartner] = useState('')
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

  function load() {
    const params = new URLSearchParams()
    if (product) params.set('product', product)
    if (partner) params.set('partner', partner)

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

  useEffect(load, [product, partner])

  function openForm() {
    setFormProduct(product || 'personal_loan')
    setFormPartner(partner)
    setFormInputType('proposed')
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

      {loadError && <p className="error">{loadError}</p>}
      {!loadError && cards.length === 0 && <p className="empty">No submissions match this filter.</p>}

      <div className="grid">
        {cards.map((card) => {
          const days = daysUntil(card.sla_due)
          const late = days !== null && days <= 0
          return (
            <div className="card" key={card.submission_id}>
              {card.image_url ? (
                <img className="thumb" src={card.image_url} alt={card.submission_id} />
              ) : (
                <div className="thumb thumb-missing">no screenshot</div>
              )}
              <div className="card-id">{card.submission_id}</div>
              <span className={'badge badge-' + card.input_type}>
                {card.input_type === 'production' ? 'Production' : 'Proposed'}
              </span>
              <div className="card-surface">{card.surface}</div>
              <div className={late ? 'sla sla-late' : 'sla'}>{slaText(days)}</div>
            </div>
          )
        })}
      </div>
    </main>
  )
}

export default InputView
