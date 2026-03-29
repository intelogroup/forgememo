import type { Run } from '@/lib/api'

export default function ActivityFeed({ runs }: { runs: Run[] }) {
  if (runs.length === 0) {
    return <p className="text-muted text-sm">No recent activity.</p>
  }
  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden">
      <div className="grid grid-cols-[120px_1fr_80px] px-3 py-2 border-b border-border text-subtle text-xs uppercase">
        <span>Time</span><span>Model</span><span className="text-right">Cost</span>
      </div>
      {runs.map((run, i) => (
        <div key={i} className="grid grid-cols-[120px_1fr_80px] px-3 py-2 border-b border-border/50 text-xs last:border-0">
          <span className="text-muted">{new Date(run.ts).toLocaleString()}</span>
          <span className="text-gray-300">{run.model}</span>
          <span className="text-right text-muted">${run.cost_usd.toFixed(4)}</span>
        </div>
      ))}
    </div>
  )
}
