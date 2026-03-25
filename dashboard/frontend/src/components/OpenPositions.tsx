import { useState } from 'react'
import { ChevronRight, ChevronDown } from 'lucide-react'
import type { Position, Timeframe } from '../types'
import { SpotlightCard } from './ui/SpotlightCard'
import { PriceChart } from './PriceChart'
import { toSGT, cn } from '../lib/utils'

const TF_BTNS: Timeframe[] = ['1m', '15m', '1h', '1d', '1w', '1M']
const TF_LABELS: Record<Timeframe, string> = {
  '1m': '1 min', '15m': '15 min', '1h': '1 hour', '1d': '1 day', '1w': '1 week', '1M': '1 month',
}
const LEVERAGE = 3
const STOP_LOSS_PCT = 0.15

function pnlColor(v: number) { return v >= 0 ? 'text-[#26c17c]' : 'text-[#e05252]' }
function badge(label: string, positive: boolean) {
  return (
    <span className={cn('text-[0.6rem] px-1.5 py-0.5 rounded font-medium', positive ? 'bg-[#26c17c22] text-[#26c17c]' : 'bg-[#e0525222] text-[#e05252]')}>
      {label}
    </span>
  )
}

interface Props {
  positions: Position[]
  prices: Record<string, number>
}

function PositionRow({ pos, prices, tf }: { pos: Position; prices: Record<string, number>; tf: Timeframe }) {
  const [expanded, setExpanded] = useState(false)

  const liveA = prices[pos.sym_a] ?? null
  const liveB = prices[pos.sym_b] ?? null

  const isLong = pos.direction === 'LONG_SPREAD'
  const legADir = isLong ? 'LONG' : 'SHORT'
  const legBDir = isLong ? 'SHORT' : 'LONG'

  const pnlA = liveA != null
    ? pos.notional_a * (isLong ? (liveA - pos.entry_price_a) / pos.entry_price_a : (pos.entry_price_a - liveA) / pos.entry_price_a)
    : (pos.pnl_a ?? 0)
  const pnlB = liveB != null
    ? pos.notional_b * (isLong ? (pos.entry_price_b - liveB) / pos.entry_price_b : (liveB - pos.entry_price_b) / pos.entry_price_b)
    : (pos.pnl_b ?? 0)
  const totalPnl = pnlA + pnlB

  const capitalTotal = pos.notional_a + pos.notional_b
  const exposureA = pos.notional_a * LEVERAGE
  const exposureB = pos.notional_b * LEVERAGE
  const slPnl = -capitalTotal * STOP_LOSS_PCT

  const fmtP = (v: number) => (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2)
  const fmtPrice = (v: number | null) => v != null ? `$${v.toFixed(4)}` : '—'

  return (
    <>
      <SpotlightCard
        className="mb-3 cursor-pointer"
        glowColor={totalPnl >= 0 ? 'green' : 'red'}
      >
        {/* Header row */}
        <div className="flex items-center justify-between mb-3" onClick={() => setExpanded(e => !e)}>
          <div className="flex items-center gap-3">
            <span className="text-white font-semibold">{pos.pair_key}</span>
            {badge(pos.direction.replace('_', ' '), isLong)}
          </div>
          <div className="flex items-center gap-4">
            <span className={cn('text-lg font-bold', pnlColor(totalPnl))}>{fmtP(totalPnl)}</span>
            {expanded ? <ChevronDown size={16} className="text-[#555]" /> : <ChevronRight size={16} className="text-[#555]" />}
          </div>
        </div>

        {/* Z-score row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3 text-sm">
          <div>
            <p className="text-[0.65rem] text-[#888] uppercase">Entry Z</p>
            <p className="text-[#7eb6ff] font-mono">{pos.entry_zscore.toFixed(2)}</p>
          </div>
          <div>
            <p className="text-[0.65rem] text-[#888] uppercase">Current Z</p>
            <p className="text-[#7eb6ff] font-mono">{pos.current_zscore != null ? pos.current_zscore.toFixed(2) : '—'}</p>
          </div>
          <div>
            <p className="text-[0.65rem] text-[#888] uppercase">Stop Loss</p>
            <p className="text-[#e05252] font-mono">{fmtP(slPnl)}</p>
          </div>
          <div>
            <p className="text-[0.65rem] text-[#888] uppercase">Exit Target</p>
            <p className="text-[#7eb6ff] font-mono">z → 0.0</p>
          </div>
        </div>

        {/* Legs */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
          {([['a', pos.sym_a, pos.entry_price_a, liveA, pnlA, legADir], ['b', pos.sym_b, pos.entry_price_b, liveB, pnlB, legBDir]] as const).map(
            ([leg, sym, entryP, liveP, pnl, dir]) => (
              <div key={leg} className="bg-[#12151e] rounded-lg p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-white font-medium text-sm">{sym}/USDT</span>
                  {badge(dir as string, dir === 'LONG')}
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <p className="text-[#888] mb-0.5">Entry</p>
                    <p className="text-[#aaa] font-mono">{fmtPrice(entryP)}</p>
                  </div>
                  <div>
                    <p className="text-[#888] mb-0.5">Live</p>
                    <p className="text-[#7eb6ff] font-mono">{fmtPrice(liveP)}</p>
                  </div>
                  <div>
                    <p className="text-[#888] mb-0.5">PnL</p>
                    <p className={cn('font-mono font-semibold', pnlColor(pnl))}>{fmtP(pnl)}</p>
                  </div>
                </div>
              </div>
            )
          )}
        </div>

        {/* Capital info */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs text-[#888] mb-3">
          <p>Capital: <span className="text-[#7eb6ff]">${pos.notional_a.toFixed(0)} + ${pos.notional_b.toFixed(0)} = ${capitalTotal.toFixed(0)}</span></p>
          <p>Exposure ({LEVERAGE}x): <span className="text-[#7eb6ff]">${exposureA.toFixed(0)} + ${exposureB.toFixed(0)} = ${(exposureA + exposureB).toFixed(0)}</span></p>
          <p>Stop loss: <span className="text-[#e05252]">-15% of capital</span></p>
        </div>

        {/* Timing */}
        <div className="flex flex-wrap gap-4 text-xs text-[#666]">
          <p>Opened: <span className="text-[#888]">{toSGT(pos.entry_time)}</span></p>
          <p>Updated: <span className="text-[#888]">{toSGT(pos.last_updated ?? undefined)}</span></p>
        </div>

        {/* Charts */}
        {expanded && (
          <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {(['a', 'b'] as const).map(leg => {
              const sym = leg === 'a' ? pos.sym_a : pos.sym_b
              const entryP = leg === 'a' ? pos.entry_price_a : pos.entry_price_b
              const liveP = leg === 'a' ? liveA : liveB
              const dir = leg === 'a' ? legADir : legBDir
              return (
                <div key={leg} className="bg-[#0f1117] rounded-lg p-3">
                  <p className="text-[0.7rem] text-[#888] uppercase mb-2">{sym}/USDT {dir}</p>
                  <PriceChart sym={sym} entryPrice={entryP} legDir={dir} livePrice={liveP} timeframe={tf} />
                </div>
              )
            })}
          </div>
        )}
      </SpotlightCard>
    </>
  )
}

export function OpenPositions({ positions, prices }: Props) {
  const [tf, setTf] = useState<Timeframe>('15m')

  if (!positions.length) return (
    <p className="text-[#888] italic mb-6">No open positions.</p>
  )

  return (
    <div className="mb-6">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[0.8rem] uppercase text-[#aaa] tracking-wide">Open Positions</h2>
        <p className="text-[0.65rem] text-[#555]">Prices update every 5s</p>
      </div>
      <div className="flex flex-wrap gap-2 mb-4">
        {TF_BTNS.map(t => (
          <button
            key={t}
            onClick={() => setTf(t)}
            className={cn(
              'px-3 py-1 rounded text-xs border transition-colors',
              tf === t
                ? 'bg-[#7eb6ff] text-[#0f1117] border-[#7eb6ff]'
                : 'bg-[#1a1d27] text-[#aaa] border-[#252836] hover:border-[#7eb6ff]'
            )}
          >
            {TF_LABELS[t]}
          </button>
        ))}
      </div>
      {positions.map(pos => (
        <PositionRow key={pos.pair_key} pos={pos} prices={prices} tf={tf} />
      ))}
    </div>
  )
}
