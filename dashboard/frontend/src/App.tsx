import { useMemo } from 'react'
import { useData, usePrices } from './hooks/useData'
import { SummaryCards } from './components/SummaryCards'
import { EquityCurve } from './components/EquityCurve'
import { OpenPositions } from './components/OpenPositions'
import { ClosedTrades } from './components/ClosedTrades'

export default function App() {
  const { summary, open_positions, closed_positions, equity_curve } = useData()

  const symbols = useMemo(() =>
    [...new Set(open_positions.flatMap(p => [p.sym_a, p.sym_b]))],
    [open_positions]
  )
  const prices = usePrices(symbols)

  return (
    <div className="min-h-screen bg-[#0f1117] p-4 sm:p-6">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-bold text-white">Crypto Paper Trading</h1>
          <span className="text-xs text-[#555]">
            {new Date().toLocaleTimeString('en-SG', { timeZone: 'Asia/Singapore' })} SGT
          </span>
        </div>

        <SummaryCards summary={summary} />
        <EquityCurve points={equity_curve} initialCapital={summary?.initial_capital ?? 10000} />
        <OpenPositions positions={open_positions} prices={prices} />
        <ClosedTrades trades={closed_positions} />
      </div>
    </div>
  )
}
