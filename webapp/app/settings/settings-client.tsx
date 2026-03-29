'use client'
import { useState } from 'react'

interface SyncTrace {
  content: string
  trace_type: string
  project_tag?: string
  ts?: string
}

interface SettingsClientProps {
  provider: string
  traces: SyncTrace[]
}

export default function SettingsClient({ provider, traces }: SettingsClientProps) {
  const [tab, setTab] = useState<'provider' | 'sync'>('provider')

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <div className="flex gap-1 mb-6 border-b border-border">
        {(['provider', 'sync'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm capitalize -mb-px border-b-2 transition-colors ${tab === t ? 'border-brand text-white' : 'border-transparent text-muted hover:text-white'}`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'provider' && (
        <div className="bg-card border border-border rounded-lg p-5">
          <div className="text-muted text-xs uppercase tracking-wide mb-1">Current Provider</div>
          <div className="text-white text-lg font-medium mb-4">{provider}</div>
          <p className="text-muted text-sm">To change your provider, run:</p>
          <code className="block bg-bg border border-border rounded px-3 py-2 text-green-400 text-xs mt-2 font-mono">
            forgemem config
          </code>
        </div>
      )}

      {tab === 'sync' && (
        <div>
          {traces.length === 0 ? (
            <div className="bg-card border border-border rounded-lg p-5">
              <p className="text-muted text-sm">No synced traces yet.</p>
              <p className="text-muted text-sm mt-2">Run <code className="text-green-400 font-mono">forgemem sync push</code> to upload your local traces.</p>
            </div>
          ) : (
            <div className="bg-card border border-border rounded-lg overflow-hidden">
              <div className="grid grid-cols-[1fr_80px_100px] px-4 py-2 border-b border-border text-subtle text-xs uppercase">
                <span>Content</span><span>Type</span><span>Project</span>
              </div>
              {traces.map((t, i) => (
                <div key={i} className="grid grid-cols-[1fr_80px_100px] px-4 py-2 border-b border-border/50 text-xs last:border-0">
                  <span className="text-gray-300 truncate">{t.content}</span>
                  <span className="text-muted">{t.trace_type}</span>
                  <span className="text-muted">{t.project_tag ?? '—'}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
