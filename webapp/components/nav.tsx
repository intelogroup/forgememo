import Link from 'next/link'
import { redirect } from 'next/navigation'

async function signOut() {
  'use server'
  const { cookies: c } = await import('next/headers')
  c().delete('fm_token')
  redirect('/login')
}

export default function Nav({ current }: { current: 'dashboard' | 'billing' | 'settings' }) {
  const navItems = [
    { href: '/dashboard', label: 'Dashboard' },
    { href: '/billing', label: 'Billing' },
    { href: '/settings', label: 'Settings' },
  ]
  return (
    <nav className="bg-card border-b border-border px-6 py-3 flex items-center gap-5">
      <span className="text-brand font-bold text-base">forgemem</span>
      {navItems.map(item => (
        <Link
          key={item.href}
          href={item.href}
          className={`text-xs px-3 py-1 rounded ${current === item.label.toLowerCase() ? 'bg-border text-muted' : 'text-subtle hover:text-muted'}`}
        >
          {item.label}
        </Link>
      ))}
      <form action={signOut} className="ml-auto">
        <button type="submit" className="text-subtle text-xs hover:text-muted">Sign out</button>
      </form>
    </nav>
  )
}
