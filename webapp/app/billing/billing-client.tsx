'use client'
import { useState } from 'react'
import CreditPack from '@/components/credit-pack'
import ActivityFeed from '@/components/activity-feed'
import type { Run } from '@/lib/api'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

const PACKS = [
  { id: 'pack_5', amount: '$5', price: '5 USD — ~1000 runs' },
  { id: 'pack_20', amount: '$20', price: '20 USD — ~4200 runs' },
  { id: 'pack_50', amount: '$50', price: '50 USD — ~11000 runs' },
]

interface BillingClientProps {
  token: string
  balance: number
  runs: Run[]
}

export default function BillingClient({ token, balance, runs }: BillingClientProps) {
  const [selected, setSelected] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleCheckout() {
    if (!selected) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/v1/checkout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ pack_id: selected }),
      })
      if (!res.ok) throw new Error(`Checkout failed (${res.status})`)
      const { url } = await res.json()
      if (!url) throw new Error('No checkout URL returned')
      window.location.href = url
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Checkout failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <div className="bg-card border border-border rounded-lg p-5 mb-6">
        <div className="text-muted text-xs uppercase tracking-wide mb-1">Current Balance</div>
        <div className="text-brand text-3xl font-bold">${balance.toFixed(2)}</div>
      </div>

      <div className="mb-6">
        <div className="text-muted text-xs uppercase tracking-wide mb-3">Top Up</div>
        <div className="grid grid-cols-3 gap-3 mb-4">
          {PACKS.map(p => (
            <CreditPack
              key={p.id}
              amount={p.amount}
              price={p.price}
              selected={selected === p.id}
              onClick={() => setSelected(p.id)}
            />
          ))}
        </div>
        {error && <p className="text-red-400 text-sm mb-3">{error}</p>}
        <button
          onClick={handleCheckout}
          disabled={!selected || loading}
          className="bg-brand hover:bg-green-700 text-white rounded px-4 py-2 text-sm font-medium disabled:opacity-50"
        >
          {loading ? 'Redirecting...' : 'Continue to payment'}
        </button>
      </div>

      <div className="text-muted text-xs uppercase tracking-wide mb-3">Usage History</div>
      <ActivityFeed runs={runs} />
    </div>
  )
}
