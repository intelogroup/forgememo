import { redirect } from 'next/navigation'
import { getSession } from '@/lib/auth'
import { getUserSettings, getSyncedTraces } from '@/lib/api'
import Nav from '@/components/nav'
import SettingsClient from './settings-client'

export default async function SettingsPage() {
  const token = getSession()
  if (!token) redirect('/login')

  const [settings, traces] = await Promise.all([
    getUserSettings(token),
    getSyncedTraces(token).catch(() => []),
  ])

  return (
    <div>
      <Nav current="settings" />
      <SettingsClient provider={settings.provider} traces={traces} />
    </div>
  )
}
