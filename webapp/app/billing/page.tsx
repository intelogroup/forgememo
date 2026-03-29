import { redirect } from 'next/navigation'
import { getSession } from '@/lib/auth'
import { getStats, getActivity } from '@/lib/api'
import Nav from '@/components/nav'
import BillingClient from './billing-client'

export default async function BillingPage() {
  const token = getSession()
  if (!token) redirect('/login')

  const [stats, runs] = await Promise.all([getStats(token), getActivity(token)])

  return (
    <div>
      <Nav current="billing" />
      <BillingClient token={token} balance={stats.balance_usd} runs={runs} />
    </div>
  )
}
