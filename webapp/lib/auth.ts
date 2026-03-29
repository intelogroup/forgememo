import { cookies } from 'next/headers'

export function getSession(): string | null {
  const cookieStore = cookies()
  return cookieStore.get('fm_token')?.value ?? null
}
