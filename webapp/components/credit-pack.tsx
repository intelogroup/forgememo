interface CreditPackProps {
  amount: string
  price: string
  selected: boolean
  onClick: () => void
}

export default function CreditPack({ amount, price, selected, onClick }: CreditPackProps) {
  return (
    <button
      onClick={onClick}
      className={`border rounded-lg p-4 text-left transition-colors ${selected ? 'border-brand bg-brand/10' : 'border-border bg-card hover:border-muted'}`}
    >
      <div className="text-white font-bold text-lg">{amount}</div>
      <div className="text-muted text-sm">{price}</div>
    </button>
  )
}
