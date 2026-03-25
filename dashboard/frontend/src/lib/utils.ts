import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function toSGT(utcIso: string | undefined): string {
  if (!utcIso) return '—'
  try {
    const dt = new Date(utcIso)
    return dt.toLocaleString('en-SG', {
      timeZone: 'Asia/Singapore',
      year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    })
  } catch {
    return utcIso.slice(0, 19)
  }
}

export function fmt(v: number, decimals = 2): string {
  return v.toFixed(decimals)
}

export function fmtUSD(v: number): string {
  return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2)
}
