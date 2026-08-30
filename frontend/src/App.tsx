import { useState } from 'react'
import './shell.css'
import InputView from './InputView'
import ReviewQueue from './ReviewQueue'
import RulebookView from './RulebookView'

type View = 'inputs' | 'rulebook' | 'queue'

// The order the work happens in: the inputs arrive, the rulebook says what they
// are judged against, and the queue is where a person judges them.
const TABS: { id: View; label: string }[] = [
  { id: 'inputs', label: 'Inputs' },
  { id: 'rulebook', label: 'Rulebook' },
  { id: 'queue', label: 'Review Queue' },
]

function App() {
  const [view, setView] = useState<View>('inputs')
  const [resetting, setResetting] = useState(false)

  /** Demo tool: put the database back to the seeded fixtures. */
  function resetDemo() {
    if (!window.confirm('Reset all demo data? This clears every AI run, decision, and upload.')) {
      return
    }
    setResetting(true)
    fetch('/api/review/reset', { method: 'POST' })
      // A gated-off server answers 404 — and so would one without the route at
      // all, whose body is HTML rather than JSON.
      .then((r) => r.json().catch(() => ({})).then((body) => ({ ok: r.ok, status: r.status, body })))
      .then(({ ok, status, body }) => {
        if (status === 404) {
          throw new Error('the demo endpoint is disabled on this server (set CLEARPATH_DEMO).')
        }
        if (!ok) throw new Error(body.error || 'reset failed')
        // Every view holds its own fetched copy of the data that just vanished;
        // a reload is the one refetch that is guaranteed to reach all of them.
        location.reload()
      })
      .catch((err: Error) => {
        setResetting(false)
        alert('Reset failed: ' + err.message)
      })
  }

  return (
    <>
      <nav className="view-tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={view === tab.id ? 'view-tab active' : 'view-tab'}
            onClick={() => setView(tab.id)}
          >
            {tab.label}
          </button>
        ))}
        <button className="reset-demo" onClick={resetDemo} disabled={resetting}>
          {resetting ? 'Resetting…' : 'Reset demo data'}
        </button>
      </nav>
      {view === 'inputs' && <InputView />}
      {view === 'rulebook' && <RulebookView />}
      {view === 'queue' && <ReviewQueue onGoToInputs={() => setView('inputs')} />}
    </>
  )
}

export default App
