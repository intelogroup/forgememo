import { redirect } from 'next/navigation'
import { getSession } from '@/lib/auth'
import { getStats, getActivity } from '@/lib/api'
import Nav from '@/components/nav'
import StatCard from '@/components/stat-card'
import ActivityFeed from '@/components/activity-feed'

export default async function DashboardPage() {
  const token = getSession()
  if (!token) redirect('/login')

  const [stats, runs] = await Promise.all([
    getStats(token),
    getActivity(token),
  ])

  const hasSyncData = stats.traces !== undefined && stats.traces > 0

  return (
    <div>
      <Nav current="dashboard" />
      <div className="max-w-3xl mx-auto px-6 py-8">
        <div className={`grid gap-4 mb-6 ${hasSyncData ? 'grid-cols-4' : 'grid-cols-2'}`}>
          <StatCard label="Total Runs" value={stats.total_runs} />
          {hasSyncData && <StatCard label="Traces" value={stats.traces!} subtitle="synced" />}
          {hasSyncData && <StatCard label="Principles" value={stats.principles!} subtitle="distilled" accent />}
          <StatCard label="Balance" value={`$${stats.balance_usd.toFixed(2)}`} accent />
        </div>

        <div className="text-muted text-xs uppercase tracking-wide mb-3">Recent Activity</div>
        <ActivityFeed runs={runs} />

        {hasSyncData && stats.projects && stats.projects.length > 0 && (
          <div className="mt-6">
            <div className="text-muted text-xs uppercase tracking-wide mb-3">Projects</div>
            <div className="bg-card border border-border rounded-lg overflow-hidden">
              {stats.projects.map(p => (
                <div key={p} className="px-4 py-2 border-b border-border/50 text-sm text-gray-300 last:border-0">{p}</div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
