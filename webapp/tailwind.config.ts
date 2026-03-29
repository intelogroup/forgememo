import type { Config } from 'tailwindcss'
export default {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: '#15803d',
        bg: '#0f1117',
        card: '#1a1f2e',
        border: '#2d3748',
        muted: '#718096',
        subtle: '#4a5568',
      },
    },
  },
} satisfies Config
