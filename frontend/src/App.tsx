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
      </nav>
      {view === 'inputs' && <InputView />}
      {view === 'queue' && <QueueView />}
      {view === 'rulebook' && <RulebookView />}
    </>
  )
}

export default App
