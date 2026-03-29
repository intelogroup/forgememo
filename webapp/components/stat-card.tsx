interface StatCardProps {
  label: string
  value: string | number
  subtitle?: string
  accent?: boolean
}

export default function StatCard({ label, value, subtitle, accent }: StatCardProps) {
  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className="text-muted text-xs uppercase tracking-wide mb-1">{label}</div>
      <div className={`text-2xl font-bold ${accent ? 'text-brand' : 'text-white'}`}>{value}</div>
      {subtitle && <div className="text-muted text-xs mt-1">{subtitle}</div>}
    </div>
  )
}
