import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import MatchDetailPage from './pages/MatchDetailPage'
import Performance from './pages/Performance'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded px-2 py-1 text-sm font-medium no-underline ${isActive ? 'text-ink underline underline-offset-4' : 'text-ink-2'}`

export default function App() {
  return (
    <div className="mx-auto max-w-4xl px-4 pb-12">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-3">
        <span className="font-semibold">Football match probabilities</span>
        <nav aria-label="Main" className="flex gap-2">
          <NavLink to="/" end className={navClass}>
            Matches
          </NavLink>
          <NavLink to="/performance" className={navClass}>
            Model performance
          </NavLink>
        </nav>
      </header>
      <main className="pt-5">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/matches/:id" element={<MatchDetailPage />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="*" element={<p className="text-sm text-ink-2">Page not found.</p>} />
        </Routes>
      </main>
      <footer className="mt-10 border-t border-line pt-3 text-xs text-ink-2">
        An analysis tool. No betting, deposits or bookmaker accounts. Results and odds data: Football-Data.co.uk. Fixture list: openfootball (public domain).
      </footer>
    </div>
  )
}
