import { useEffect, useRef, useState } from 'react'
import { Chart, registerables } from 'chart.js'
import 'chartjs-adapter-date-fns'
import annotationPlugin from 'chartjs-plugin-annotation'
import type { EquityPoint } from '../types'
import { ChevronDown, ChevronRight } from 'lucide-react'

Chart.register(...registerables, annotationPlugin)

interface Props {
  points: EquityPoint[]
  initialCapital: number
}

export function EquityCurve({ points, initialCapital }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    if (!canvasRef.current || !points.length) return
    if (chartRef.current) chartRef.current.destroy()

    const data = points.map(p => ({ x: new Date(p.ts).getTime(), y: p.equity }))

    chartRef.current = new Chart(canvasRef.current, {
      type: 'line',
      data: {
        datasets: [{
          data,
          borderColor: '#7eb6ff',
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          backgroundColor: 'rgba(126,182,255,0.08)',
        }],
      },
      options: {
        parsing: false,
        animation: false,
        plugins: {
          legend: { display: false },
          annotation: {
            annotations: {
              initialLine: {
                type: 'line',
                yMin: initialCapital,
                yMax: initialCapital,
                borderColor: 'rgba(255,255,255,0.25)',
                borderWidth: 1.5,
                borderDash: [6, 4],
                label: {
                  display: true,
                  content: `Initial $${initialCapital.toLocaleString()}`,
                  position: 'start',
                  color: '#aaa',
                  backgroundColor: 'rgba(0,0,0,0.4)',
                  font: { size: 10 },
                },
              },
            },
          },
        },
        scales: {
          x: {
            type: 'time',
            time: {
              tooltipFormat: 'MMM d HH:mm',
              displayFormats: { millisecond: 'HH:mm', second: 'HH:mm', minute: 'HH:mm', hour: 'MMM d HH:mm', day: 'MMM d' },
            },
            ticks: { color: '#555', maxTicksLimit: 8 },
            grid: { color: '#1e2130' },
          },
          y: {
            ticks: { color: '#555', callback: (v) => `$${Number(v).toLocaleString()}` },
            grid: { color: '#1e2130' },
          },
        },
      },
    })
    return () => { chartRef.current?.destroy() }
  }, [points, initialCapital])

  return (
    <div className="bg-[#1a1d27] rounded-xl p-4 mb-6">
      <button
        onClick={() => setCollapsed(c => !c)}
        className="flex items-center gap-2 text-[0.8rem] uppercase text-[#aaa] tracking-wide mb-3 hover:text-white transition-colors"
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        Equity Curve
      </button>
      {!collapsed && (
        points.length
          ? <canvas ref={canvasRef} height={80} />
          : <p className="text-[#888] italic text-sm">No equity curve data yet.</p>
      )}
    </div>
  )
}
