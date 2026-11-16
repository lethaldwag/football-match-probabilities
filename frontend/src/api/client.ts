import type { DataStatus, Evaluation, MatchDetail, MatchPage } from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  const url = `${BASE}/api/v1${path}${query.size ? `?${query}` : ''}`
  let response: Response
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' } })
  } catch {
    throw new ApiError(0, 'network', 'Could not reach the server. Check that the API is running.')
  }
  if (!response.ok) {
    // The API always answers errors as {error: {code, message, details}}.
    const body = await response.json().catch(() => null)
    const error = body?.error
    throw new ApiError(response.status, error?.code ?? 'error', error?.message ?? `Request failed (${response.status})`)
  }
  return (await response.json()) as T
}

export interface MatchQuery {
  competition: string
  status?: string
  date_from?: string
  date_to?: string
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

const liveApi = {
  status: (competition: string) => get<DataStatus>('/status', { competition }),
  matches: (query: MatchQuery) => get<MatchPage>('/matches', { ...query }),
  match: (id: number) => get<MatchDetail>(`/matches/${id}`),
  evaluation: (competition: string) => get<Evaluation>('/evaluation/latest', { competition }),
}

// --- Static hosting (e.g. GitHub Pages) ------------------------------------------------------------
// With VITE_STATIC_DATA=true there is no API server. The same responses are read from JSON files
// written by `python -m app.cli export`, and match filtering/paging happens in the browser.

const files = new Map<string, Promise<unknown>>()

function file<T>(name: string, notFound: string): Promise<T> {
  let pending = files.get(name) as Promise<T> | undefined
  if (!pending) {
    pending = (async () => {
      let response: Response
      try {
        response = await fetch(`${import.meta.env.BASE_URL}data/${name}`, { headers: { Accept: 'application/json' } })
      } catch {
        throw new ApiError(0, 'network', 'Could not load the data. Check your connection and try again.')
      }
      if (response.status === 404) throw new ApiError(404, 'not_found', notFound)
      if (!response.ok) throw new ApiError(response.status, 'error', `Request failed (${response.status})`)
      return (await response.json()) as T
    })()
    files.set(name, pending)
    pending.catch(() => files.delete(name)) // a failed load can be retried
  }
  return pending
}

const staticApi: typeof liveApi = {
  status: (competition) => file(`status-${competition}.json`, `No data has been published for '${competition}' yet`),
  matches: async ({ competition, status = 'scheduled', date_from, date_to, order = 'asc', limit = 20, offset = 0 }) => {
    const all = await file<MatchPage>(`matches-${competition}-${status}.json`, 'No matches have been published yet')
    // Files are sorted by kick-off ascending; dates are compared as UTC days, like the API does.
    const items = all.items.filter((m) => {
      const day = m.kickoff_utc.slice(0, 10)
      return (!date_from || day >= date_from) && (!date_to || day <= date_to)
    })
    if (order === 'desc') items.reverse()
    return { items: items.slice(offset, offset + limit), total: items.length, limit, offset }
  },
  match: (id) => file(`match/${id}.json`, `Match ${id} not found`),
  evaluation: (competition) =>
    file(`evaluation-${competition}.json`, 'No evaluation has been run for this competition yet'),
}

export const STATIC_DATA = import.meta.env.VITE_STATIC_DATA === 'true'

export const api = STATIC_DATA ? staticApi : liveApi
