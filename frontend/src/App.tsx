import { useState } from 'react'
import './shell.css'
import InputView from './InputView'
import QueueView from './QueueView'

function App() {
  const [view, setView] = useState<'inputs' | 'queue'>('inputs')

  return (
    <>
      <nav className="view-tabs">
        <button
          className={view === 'inputs' ? 'view-tab active' : 'view-tab'}
          onClick={() => setView('inputs')}
        >
          Inputs
        </button>
        <button
          className={view === 'queue' ? 'view-tab active' : 'view-tab'}
          onClick={() => setView('queue')}
        >
          Queue
        </button>
      </nav>
      {view === 'inputs' ? <InputView /> : <QueueView />}
    </>
  )
}

export default App
