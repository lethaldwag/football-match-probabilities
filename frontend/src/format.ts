import type { Market } from './api/types'

export const MARKET_LABELS: Record<Market, string> = {
  '1X2': 'Match result',
  OU25: 'Total goals over/under 2.5',
  BTTS: 'Both teams to score',
}

export const SELECTION_LABELS: Record<string, string> = {
  home: 'Home win',
  draw: 'Draw',
  away: 'Away win',
  over: 'Over 2.5',
  under: 'Under 2.5',
  yes: 'Yes',
  no: 'No',
}

// Bookmaker codes exactly as used by the data provider's files; unknown codes are shown as-is.
const BOOKMAKERS: Record<string, string> = {
  Avg: 'Market average',
  Max: 'Market maximum',
  B365: 'Bet365',
  PS: 'Pinnacle',
  BbAv: 'Betbrain average',
  BbMx: 'Betbrain maximum',
}

export const bookmakerName = (code: string) => BOOKMAKERS[code] ?? code

export const MODEL_NAMES: Record<string, string> = {
  'poisson-goals': 'Poisson goals model',
  'base-rate': 'Base-rate reference',
  'bookmaker-implied': 'Bookmaker-implied (benchmark)',
}

export const percent = (p: number, digits = 0) => `${(p * 100).toFixed(digits)}%`

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return 'not available'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function kickoff(iso: string, precision: 'datetime' | 'date'): string {
  const date = new Date(iso)
  if (precision === 'date') {
    return `${date.toLocaleDateString(undefined, { dateStyle: 'medium', timeZone: 'UTC' })} (time unknown)`
  }
  return date.toLocaleString(undefined, { weekday: 'short', dateStyle: undefined, day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}
