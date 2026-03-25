import { useEffect, useRef, useCallback } from 'react'
import { Chart, registerables } from 'chart.js'
import 'chartjs-adapter-date-fns'
import annotationPlugin from 'chartjs-plugin-annotation'
import type { Timeframe } from '../types'

Chart.register(...registerables, annotationPlugin)

const COLORS = ['#7eb6ff', '#26c17c', '#f5a623', '#e05252', '#b48eff', '#50e3c2']
let colorIdx = 0

const TF_CFG: Record<Timeframe, { interval: string; limit: number; unit: string; fmt: string }> = {
  '1m':  { interval: '1m',  limit: 60,  unit: 'minute', fmt: 'HH:mm' },
  '15m': { interval: '1m',  limit: 900, unit: 'minute', fmt: 'HH:mm' },
  '1h':  { interval: '5m',  limit: 720, unit: 'hour',   fmt: 'HH:mm' },
  '1d':  { interval: '1h',  limit: 24,  unit: 'hour',   fmt: 'MMM d' },
  '1w':  { interval: '4h',  limit: 42,  unit: 'day',    fmt: 'MMM d' },
  '1M':  { interval: '1d',  limit: 30,  unit: 'day',    fmt: 'MMM d' },
}

interface Props {
  sym: string
  entryPrice: number
  legDir: 'LONG' | 'SHORT'
  livePrice: number | null
  timeframe: Timeframe
}

export function PriceChart({ sym, entryPrice, legDir, livePrice, timeframe }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)
  const historyRef = useRef<{ ts: number; price: number }[]>([])
  const color = useRef(COLORS[colorIdx++ % COLORS.length])

  const buildChart = useCallback(() => {
    if (!canvasRef.current) return
    if (chartRef.current) chartRef.current.destroy()
    chartRef.current = new Chart(canvasRef.current, {
      type: 'line',
      data: { datasets: [{ data: [], borderColor: color.current, borderWidth: 1.5, pointRadius: 0, fill: false }] },
      options: {
        parsing: false, animation: false,
        plugins: {
          legend: { display: false },
          annotation: {
            annotations: {
              entryLine: {
                type: 'line', yMin: entryPrice, yMax: entryPrice,
                borderColor: '#f5a623', borderWidth: 1.5, borderDash: [6, 3],
                label: { display: true, content: `Entry $${entryPrice.toPrecision(5)}`, position: 'start', color: '#f5a623', backgroundColor: 'rgba(245,166,35,0.15)', font: { size: 10 } },
              },
              currentLine: {
                type: 'line', yMin: 0, yMax: 0,
                borderColor: legDir === 'LONG' ? '#26c17c' : '#e05252',
                borderWidth: 1, borderDash: [3, 3],
              },
            },
          },
        },
        scales: {
          x: {
            type: 'time',
            time: { tooltipFormat: 'HH:mm:ss', displayFormats: { minute: TF_CFG[timeframe].fmt, hour: TF_CFG[timeframe].fmt, day: TF_CFG[timeframe].fmt } },
            ticks: { color: '#555', maxTicksLimit: 8, maxRotation: 0 },
            grid: { color: '#1e2130' },
          },
          y: { ticks: { color: '#555' }, grid: { color: '#1e2130' } },
        },
      },
    })
  }, [entryPrice, legDir, timeframe])

  // Load history
  useEffect(() => {
    buildChart()
    fetch(`/api/history?symbol=${sym}&window=${timeframe}`)
      .then(r => r.json())
      .then((rows: { ts: number; price: number }[]) => {
        historyRef.current = rows
        if (chartRef.current) {
          chartRef.current.data.datasets[0].data = rows.map(r => ({ x: r.ts, y: r.price }))
          chartRef.current.update()
        }
      })
      .catch(() => {})
    return () => chartRef.current?.destroy()
  }, [sym, timeframe, buildChart])

  // Append live price
  useEffect(() => {
    if (livePrice == null || !chartRef.current) return
    historyRef.current.push({ ts: Date.now(), price: livePrice })
    if (historyRef.current.length > 17280) historyRef.current.shift()
    const ann = chartRef.current.options.plugins?.annotation?.annotations as Record<string, unknown>
    if (ann?.currentLine) {
      (ann.currentLine as { yMin: number; yMax: number }).yMin = livePrice;
      (ann.currentLine as { yMin: number; yMax: number }).yMax = livePrice;
    }
    chartRef.current.data.datasets[0].data = historyRef.current.map(r => ({ x: r.ts, y: r.price }))
    chartRef.current.update()
  }, [livePrice])

  return <canvas ref={canvasRef} height={120} />
}
