import type { ClosedTrade } from '../types'
import { toSGT, cn } from '../lib/utils'

interface Props { trades: ClosedTrade[] }

export function ClosedTrades({ trades }: Props) {
  if (!trades.length) return <p className="text-[#888] italic mb-6">No closed trades yet.</p>

  const recent = [...trades].reverse().slice(0, 20)

  return (
    <div className="mb-6">
      <h2 className="text-[0.8rem] uppercase text-[#aaa] tracking-wide mb-3">Recent Trades (last 20)</h2>
      <div className="overflow-x-auto rounded-xl bg-[#1a1d27]">
        <table className="w-full min-w-[600px] border-collapse">
          <thead>
            <tr className="bg-[#12151e]">
              {['Pair', 'Direction', 'Entry Z', 'PnL', 'Reason', 'Opened (SGT)', 'Closed (SGT)'].map(h => (
                <th key={h} className="text-left text-[0.65rem] uppercase text-[#888] px-3 py-2 whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {recent.map((t, i) => (
              <tr key={i} className="border-t border-[#252836] hover:bg-[#20243a] transition-colors">
                <td className="px-3 py-2 text-xs font-medium text-white">{t.pair_key}</td>
                <td className="px-3 py-2 text-xs text-[#aaa]">{t.direction}</td>
                <td className="px-3 py-2 text-xs font-mono text-[#7eb6ff]">{t.entry_zscore.toFixed(2)}</td>
                <td className={cn('px-3 py-2 text-xs font-mono font-semibold', t.realized_pnl >= 0 ? 'text-[#26c17c]' : 'text-[#e05252]')}>
                  {t.realized_pnl >= 0 ? '+' : ''}${t.realized_pnl.toFixed(2)}
                </td>
                <td className="px-3 py-2 text-xs text-[#888]">{t.reason}</td>
                <td className="px-3 py-2 text-xs text-[#666] whitespace-nowrap">{toSGT(t.entry_time)}</td>
                <td className="px-3 py-2 text-xs text-[#666] whitespace-nowrap">{toSGT(t.exit_time)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
