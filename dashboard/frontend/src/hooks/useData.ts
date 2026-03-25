import { useEffect, useState, useCallback } from 'react'
import type { Position, ClosedTrade, Summary, EquityPoint } from '../types'

interface State {
  summary: Summary | null
  open_positions: Position[]
  closed_positions: ClosedTrade[]
  equity_curve: EquityPoint[]
}

export function useData() {
  const [data, setData] = useState<State>({
    summary: null,
    open_positions: [],
    closed_positions: [],
    equity_curve: [],
  })

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch('/api/state')
      const json = await res.json()
      setData(json)
    } catch {}
  }, [])

  useEffect(() => {
    fetch_()
    const id = setInterval(fetch_, 60_000)
    return () => clearInterval(id)
  }, [fetch_])

  return data
}

export function usePrices(symbols: string[]) {
  const [prices, setPrices] = useState<Record<string, number>>({})

  useEffect(() => {
    if (!symbols.length) return
    const fetch_ = async () => {
      try {
        const res = await fetch(`/api/prices?symbols=${symbols.join(',')}`)
        const json = await res.json()
        const parsed: Record<string, number> = {}
        for (const [k, v] of Object.entries(json)) {
          if (v != null) parsed[k] = parseFloat(v as string)
        }
        setPrices(parsed)
      } catch {}
    }
    fetch_()
    const id = setInterval(fetch_, 5_000)
    return () => clearInterval(id)
  }, [symbols.join(',')])

  return prices
}
