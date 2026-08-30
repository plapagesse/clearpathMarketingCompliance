import { useState } from 'react'
import './shell.css'
import InputView from './InputView'
import QueueView from './QueueView'
import RulebookView from './RulebookView'

type View = 'inputs' | 'queue' | 'rulebook'

const TABS: { id: View; label: string }[] = [
  { id: 'inputs', label: 'Inputs' },
  { id: 'queue', label: 'Queue' },
  { id: 'rulebook', label: 'Rulebook' },
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
      .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
      .then(({ ok, body }) => {
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
      {view === 'queue' && <QueueView />}
      {view === 'rulebook' && <RulebookView />}
    </>
  )
}

export default App
