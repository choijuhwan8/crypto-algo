export interface Position {
  pair_key: string
  sym_a: string
  sym_b: string
  direction: string
  entry_zscore: number
  current_zscore: number | null
  entry_price_a: number
  entry_price_b: number
  current_price_a: number | null
  current_price_b: number | null
  notional_a: number
  notional_b: number
  pnl: number
  pnl_a: number | null
  pnl_b: number | null
  entry_time: string
  last_updated: string | null
  status: string
}

export interface ClosedTrade {
  pair_key: string
  direction: string
  entry_zscore: number
  realized_pnl: number
  reason: string
  entry_time: string
  exit_time: string
}

export interface Summary {
  equity: number
  initial_capital: number
  total_return_pct: number
  drawdown_pct: number
  win_rate: number
  total_trades: number
  peak_equity: number
  total_fees: number
}

export interface EquityPoint {
  ts: string
  equity: number
}

export type Timeframe = '1m' | '15m' | '1h' | '1d' | '1w' | '1M'
