'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { sendMagicLink } from '@/lib/api'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const router = useRouter()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      await sendMagicLink(email)
      router.push('/auth/verify')
    } catch {
      setError('Failed to send link. Check your email and try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="text-brand text-2xl font-bold mb-1">forgemem</div>
          <p className="text-muted text-sm">Sign in to your account</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            required
            placeholder="you@example.com"
            value={email}
            onChange={e => setEmail(e.target.value)}
            className="w-full bg-card border border-border rounded px-3 py-2 text-sm text-white placeholder-subtle focus:outline-none focus:border-brand"
          />
          {error && <p className="text-red-400 text-xs">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-brand hover:bg-green-700 text-white rounded px-3 py-2 text-sm font-medium disabled:opacity-60"
          >
            {loading ? 'Sending...' : 'Send magic link'}
          </button>
        </form>
      </div>
    </div>
  )
}
