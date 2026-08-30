// The Review Queue: the individualized detail view, plus a cycle through the
// submissions nobody has decided yet.
//
// It has no layout of its own any more. Clicking the tab drops the reviewer
// straight into SubmissionDetail for the oldest undecided submission; the only
// thing this file adds above it is a slim row of the same four selectors the
// input grid offers — which scope the cycle set rather than a grid — and the
// controls for moving through that set. Deciding advances; Next / Skip passes
// without deciding and wraps.

import { useEffect, useState } from 'react'
import './App.css'
import './queue.css'
import { AI_STATUSES, INPUT_TYPES, PRODUCTS } from './filters'
import SubmissionDetail, { type SubmissionCard } from './SubmissionDetail'

function ReviewQueue({ onGoToInputs }: { onGoToInputs: () => void }) {
  const [product, setProduct] = useState('')
  const [partner, setPartner] = useState('')
  const [inputType, setInputType] = useState('')
  const [aiStatus, setAiStatus] = useState('')

  const [partners, setPartners] = useState<string[]>([])
  const [items, setItems] = useState<SubmissionCard[]>([])
  const [cursor, setCursor] = useState(0)
  const [reload, setReload] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Products are a closed set (the rulebooks the engine has), so they come from
  // the shared option list and read the same as they do in Inputs. Partners are
  // whatever the data holds, so they are fetched.
  useEffect(() => {
    fetch('/api/queue/filters')
      .then((r) => r.json())
      .then((d) => setPartners(d.partners || []))
      .catch(() => setError('Could not load the partner list.'))
  }, [])

  useEffect(() => {
    const params = new URLSearchParams()
    if (product) params.set('product', product)
    if (partner) params.set('partner', partner)
    if (inputType) params.set('input_type', inputType)
    if (aiStatus) params.set('ai_status', aiStatus)

    setLoading(true)
    setError('')
    fetch('/api/queue?' + params.toString())
      .then((r) => r.json())
      .then((d) => {
        const list: SubmissionCard[] = d.items || []
        setItems(list)
        // Clamp rather than reset: the endpoint returns undecided submissions
        // only, so after a decision the item at this index has dropped out and
        // the same index is already the next one to review. Past the end, wrap.
        setCursor((c) => (c < list.length ? c : 0))
      })
      .catch(() => setError('Could not load the queue.'))
      .then(() => setLoading(false))
  }, [product, partner, inputType, aiStatus, reload])

  const current = items[cursor] || null

  return (
    <main className="page">
      {/* Every selector also resets the cursor: a re-scoped cycle starts at its
          own oldest item, not at whatever index the previous one had reached. */}
      <div className="topbar">
        <label className="filter">
          Product
          <select
            value={product}
            onChange={(e) => {
              setCursor(0)
              setProduct(e.target.value)
            }}
          >
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

        <label className="filter">
          Input type
          <select
            value={inputType}
            onChange={(e) => {
              setCursor(0)
              setInputType(e.target.value)
            }}
          >
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
          <select
            value={aiStatus}
            onChange={(e) => {
              setCursor(0)
              setAiStatus(e.target.value)
            }}
          >
            <option value="">All statuses</option>
            {AI_STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>

        {/* Counted off the cycle set itself rather than the payload's own
            `remaining`, so the number and the list cannot drift apart. */}
        <strong className="queue-remaining">{items.length} remaining</strong>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p className="empty">Loading the queue…</p>}

      {!loading && !current && (
        <p className="queue-done">
          Nothing left to review 🎉{' '}
          <button className="linkish" onClick={onGoToInputs}>
            Back to inputs
          </button>
        </p>
      )}

      {current && (
        <SubmissionDetail
          // Keyed on the id: moving to the next submission remounts, so no note
          // or findings from the one just decided can survive into it.
          key={current.submission_id}
          submissionId={current.submission_id}
          seed={current}
          // Deliberately no onProcessed: an AI run does not change what is in
          // the cycle (only a decision does), and refetching here would yank a
          // just-checked item out from under a reviewer scoped to "Not checked"
          // before they could read the findings they waited 40 seconds for.
          // SubmissionDetail refreshes itself; the stale seed is superseded.
          //
          // A decision, though, drops the item from the queue — so refetch, and
          // the cursor is left pointing at what is now the next one to review.
          onDecided={() => setReload((n) => n + 1)}
          // Passing without deciding, wrapping at the end. Nothing to offer when
          // the cycle is one item long.
          onSkip={items.length > 1 ? () => setCursor((c) => (c + 1) % items.length) : undefined}
        />
      )}
    </main>
  )
}

export default ReviewQueue
