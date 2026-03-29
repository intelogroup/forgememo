const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, { credentials: 'include', ...options })
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export interface Stats {
  total_runs: number
  balance_usd: number
  traces?: number
  principles?: number
  projects?: string[]
}

export interface Run {
  model: string
  cost_usd: number
  ts: string
}

export interface UserSettings {
  provider: string
}

export function getStats(token: string): Promise<Stats> {
  return apiFetch<Stats>('/v1/stats', { headers: { Authorization: `Bearer ${token}` } })
}

export function getActivity(token: string): Promise<Run[]> {
  return apiFetch<Run[]>('/v1/activity', { headers: { Authorization: `Bearer ${token}` } })
}

export function getUserSettings(token: string): Promise<UserSettings> {
  return apiFetch<UserSettings>('/v1/user/settings', { headers: { Authorization: `Bearer ${token}` } })
}

export function getSyncedTraces(token: string) {
  return apiFetch<any[]>('/v1/sync/pull', { headers: { Authorization: `Bearer ${token}` } })
}

export async function sendMagicLink(email: string): Promise<void> {
  const callbackUrl = `${window.location.origin}/auth/callback`
  const res = await fetch(`${API}/webapp-auth/send-link`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, callback_url: callbackUrl }),
  })
  if (!res.ok) throw new Error(`Failed to send link: ${res.status}`)
}
