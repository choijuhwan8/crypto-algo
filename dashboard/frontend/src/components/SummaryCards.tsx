import { SpotlightCard } from './ui/SpotlightCard'
import type { Summary } from '../types'
import { cn } from '../lib/utils'

interface Props { summary: Summary | null }

function Card({ label, value, color = 'neu', glow }: {
  label: string
  value: string
  color?: 'pos' | 'neg' | 'neu'
  glow?: 'blue' | 'green' | 'red' | 'purple' | 'orange'
}) {
  const textColor = { pos: 'text-[#26c17c]', neg: 'text-[#e05252]', neu: 'text-[#7eb6ff]' }[color]
  return (
    <SpotlightCard className="flex-1 min-w-[130px]" glowColor={glow ?? 'blue'}>
      <p className="text-[0.68rem] uppercase text-[#888] mb-1 tracking-wide">{label}</p>
      <p className={cn('text-2xl font-bold', textColor)}>{value}</p>
    </SpotlightCard>
  )
}

export function SummaryCards({ summary }: Props) {
  if (!summary) return (
    <p className="text-[#888] italic mb-6">No state data yet — waiting for bot to run.</p>
  )
  const ret = summary.total_return_pct
  const dd = summary.drawdown_pct
  return (
    <div className="flex flex-wrap gap-3 mb-6">
      <Card label="Equity" value={`$${summary.equity.toLocaleString('en', { minimumFractionDigits: 2 })}`} color="neu" glow="blue" />
      <Card label="Total Return" value={`${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%`} color={ret >= 0 ? 'pos' : 'neg'} glow={ret >= 0 ? 'green' : 'red'} />
      <Card label="Drawdown" value={`${dd.toFixed(2)}%`} color="neg" glow="red" />
      <Card label="Win Rate" value={`${summary.win_rate.toFixed(1)}%`} color={summary.win_rate >= 50 ? 'pos' : 'neg'} glow={summary.win_rate >= 50 ? 'green' : 'orange'} />
      <Card label="Total Trades" value={String(summary.total_trades)} color="neu" glow="purple" />
      <Card label="Peak Equity" value={`$${summary.peak_equity.toLocaleString('en', { minimumFractionDigits: 2 })}`} color="neu" glow="blue" />
      <Card label="Fees Paid" value={`$${summary.total_fees.toFixed(2)}`} color="neg" glow="orange" />
    </div>
  )
}
