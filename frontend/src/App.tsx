import { useEffect, useState } from 'react'
import './App.css'

function App() {
  const [health, setHealth] = useState<string>('checking…')

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then((d) => setHealth(d.status))
      .catch(() => setHealth('unreachable'))
  }, [])

  return (
    <main className="shell">
      <h1>ClearPath Marketing Compliance</h1>
      <p>AI-assisted compliance review for partner marketing placements.</p>
      <p className="health">
        API health: <code>{health}</code>
      </p>
    </main>
  )
}

export default App
