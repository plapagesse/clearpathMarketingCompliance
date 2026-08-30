import { useEffect, useState, type FormEvent } from 'react'
import './rulebook.css'
import './findings.css' // severity chips, shared with the findings list

type Rule = {
  rule_id: string
  product: string
  severity: string
  kind: string
  description: string
  citation: string
  url: string
}

type ClaimTypeField = {
  name: string
  type: string
  optional: boolean
  values?: string[]
}

type ClaimType = {
  name: string
  definition: string
  fields: ClaimTypeField[]
}

type Proposal = {
  id: string
  product: string
  title: string
  description: string
  severity: string
  citation_url: string | null
  rationale: string
  status: string
  created_at: string
}

const PRODUCTS = [
  { value: 'personal_loan', label: 'Personal loan' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'mortgage_prequal', label: 'Mortgage prequal' },
]

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']

function productLabel(value: string) {
  const found = PRODUCTS.find((p) => p.value === value)
  return found ? found.label : value
}

function RuleList({ rules }: { rules: Rule[] }) {
  if (rules.length === 0) return <p className="rb-empty">No rules of this kind for this product.</p>
  return (
    <ul className="rb-rules">
      {rules.map((rule) => (
        <li className="rb-rule" key={rule.rule_id}>
          {/* The plain-English line leads: a compliance officer should never
              have to read a rule id to know what the rule does. */}
          <p className="rb-rule-description">{rule.description}</p>
          <p className="rb-rule-meta">
            <span className={'chip chip-' + rule.severity}>{rule.severity}</span>
            <span className="rb-product">{productLabel(rule.product)}</span>
            <a href={rule.url} target="_blank" rel="noreferrer">
              {rule.citation}
            </a>
            <span className="rb-rule-id">{rule.rule_id}</span>
          </p>
        </li>
      ))}
    </ul>
  )
}

function RulebookView() {
  const [version, setVersion] = useState('')
  const [rules, setRules] = useState<Rule[]>([])
  const [claimTypes, setClaimTypes] = useState<ClaimType[]>([])
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [loadError, setLoadError] = useState('')

  const [product, setProduct] = useState('')

  const [formProduct, setFormProduct] = useState('personal_loan')
  const [formTitle, setFormTitle] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formSeverity, setFormSeverity] = useState('high')
  const [formCitation, setFormCitation] = useState('')
  const [formRationale, setFormRationale] = useState('')
  const [formError, setFormError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetch('/api/rulebook')
      .then((r) => r.json())
      .then((d) => {
        setVersion(d.version || '')
        setRules(d.rules || [])
        setClaimTypes(d.claim_types || [])
      })
      .catch(() => setLoadError('Could not load the rulebook.'))
  }, [])

  function loadProposals() {
    fetch('/api/rulebook/proposals')
      .then((r) => r.json())
      .then(setProposals)
      .catch(() => setProposals([]))
  }

  useEffect(loadProposals, [])

  function submitProposal(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setFormError('')
    fetch('/api/rulebook/proposals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        product: formProduct,
        title: formTitle,
        description: formDescription,
        severity: formSeverity,
        citation_url: formCitation,
        rationale: formRationale,
      }),
    })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data })))
      .then((result) => {
        if (!result.ok) throw new Error(result.data.error || 'Could not save the proposal.')
        setFormTitle('')
        setFormDescription('')
        setFormCitation('')
        setFormRationale('')
        loadProposals()
      })
      .catch((err: Error) => setFormError(err.message))
      .then(() => setSaving(false))
  }

  const shown = product ? rules.filter((r) => r.product === product) : rules
  const deterministic = shown.filter((r) => r.kind === 'deterministic')
  const judged = shown.filter((r) => r.kind === 'llm_judged')

  return (
    <main className="rulebook">
      <div className="rb-topbar">
        <h1>Rulebook</h1>
        <label className="rb-filter">
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
      </div>

      <p className="rb-intro">
        These rules are versioned data, not code — the checker loads exactly what is listed here.
        A proposal is a request: it is promoted into the rulebook through review, never applied
        live.
      </p>

      {loadError && <p className="error">{loadError}</p>}

      {version && (
        <p className="rb-version">
          Version {version} · {shown.length} rule{shown.length === 1 ? '' : 's'} shown
        </p>
      )}

      <section>
        <h2>Automated checks</h2>
        <p className="rb-lede">
          Run mechanically on every submission — same input, same finding, every time.
        </p>
        <RuleList rules={deterministic} />
      </section>

      <section>
        <h2>AI-judged checks</h2>
        <p className="rb-lede">
          Questions of impression and context, put to a model against the cited authority.
        </p>
        <RuleList rules={judged} />
      </section>

      <section>
        <h2>Claim types</h2>
        <p className="rb-lede">
          What the extractor looks for in a creative. Each type names the body of law that governs
          the claim, and the fields it records about it.
        </p>
        <ul className="rb-claim-types">
          {claimTypes.map((ct) => (
            <li key={ct.name}>
              <h3>{ct.name.replace(/_/g, ' ')}</h3>
              <p className="rb-definition">{ct.definition}</p>
              {ct.fields.length > 0 && (
                <ul className="rb-fields">
                  {ct.fields.map((f) => (
                    <li key={f.name}>
                      <span className="rb-field-name">{f.name}</span>
                      <span className="rb-field-type">{f.type}</span>
                      <span className="rb-field-optional">{f.optional ? 'optional' : 'required'}</span>
                      {f.values && <span className="rb-field-values">{f.values.join(' · ')}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>Propose a rule</h2>
        <form className="rb-form" onSubmit={submitProposal}>
          <label className="rb-field">
            Product
            <select value={formProduct} onChange={(e) => setFormProduct(e.target.value)}>
              {PRODUCTS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>

          <label className="rb-field">
            Title
            <input
              type="text"
              value={formTitle}
              placeholder="Flag 'no interest' without the deferred-interest terms"
              onChange={(e) => setFormTitle(e.target.value)}
            />
          </label>

          <label className="rb-field">
            What should it check
            <textarea
              rows={3}
              value={formDescription}
              onChange={(e) => setFormDescription(e.target.value)}
            />
          </label>

          <label className="rb-field">
            Severity
            <select value={formSeverity} onChange={(e) => setFormSeverity(e.target.value)}>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          <label className="rb-field">
            Citation URL
            <input
              type="text"
              value={formCitation}
              placeholder="https://www.consumerfinance.gov/…"
              onChange={(e) => setFormCitation(e.target.value)}
            />
          </label>

          <label className="rb-field">
            Why now
            <textarea
              rows={2}
              value={formRationale}
              onChange={(e) => setFormRationale(e.target.value)}
            />
          </label>

          {formError && <p className="error">{formError}</p>}

          <button type="submit" disabled={saving}>
            {saving ? 'Saving…' : 'Submit proposal'}
          </button>
        </form>
      </section>

      <section>
        <h2>Proposed rules (pending review)</h2>
        {proposals.length === 0 ? (
          <p className="rb-empty">Nothing proposed yet.</p>
        ) : (
          <ul className="rb-rules">
            {proposals.map((p) => (
              <li className="rb-rule" key={p.id}>
                <p className="rb-rule-description">{p.title}</p>
                {p.description && <p className="rb-definition">{p.description}</p>}
                <p className="rb-rule-meta">
                  <span className="rb-pending">{p.status}</span>
                  <span className={'chip chip-' + p.severity}>{p.severity}</span>
                  <span className="rb-product">{productLabel(p.product)}</span>
                  {p.citation_url && (
                    <a href={p.citation_url} target="_blank" rel="noreferrer">
                      citation
                    </a>
                  )}
                  <span className="rb-rule-id">{p.created_at.slice(0, 10)}</span>
                </p>
                {p.rationale && <p className="rb-definition">{p.rationale}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  )
}

export default RulebookView
