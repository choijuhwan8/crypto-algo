import { useEffect, useRef, type ReactNode } from 'react'
import type React from 'react'
import { cn } from '@/lib/utils'

interface SpotlightCardProps {
  children: ReactNode
  className?: string
  glowColor?: 'blue' | 'purple' | 'green' | 'red' | 'orange'
}

const glowColorMap = {
  blue:   { base: 220, spread: 200 },
  purple: { base: 280, spread: 300 },
  green:  { base: 120, spread: 200 },
  red:    { base: 0,   spread: 200 },
  orange: { base: 30,  spread: 200 },
}

export const SpotlightCard: React.FC<SpotlightCardProps> = ({
  children,
  className = '',
  glowColor = 'blue',
}) => {
  const cardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const sync = (e: PointerEvent) => {
      if (!cardRef.current) return
      cardRef.current.style.setProperty('--x', e.clientX.toFixed(2))
      cardRef.current.style.setProperty('--xp', (e.clientX / window.innerWidth).toFixed(2))
      cardRef.current.style.setProperty('--y', e.clientY.toFixed(2))
      cardRef.current.style.setProperty('--yp', (e.clientY / window.innerHeight).toFixed(2))
    }
    document.addEventListener('pointermove', sync)
    return () => document.removeEventListener('pointermove', sync)
  }, [])

  const { base, spread } = glowColorMap[glowColor]

  return (
    <>
      <style>{`
        [data-glow]::before,[data-glow]::after{
          pointer-events:none;content:"";position:absolute;
          inset:calc(var(--border-size)*-1);
          border:var(--border-size) solid transparent;
          border-radius:calc(var(--radius)*1px);
          background-attachment:fixed;
          background-size:calc(100% + (2*var(--border-size))) calc(100% + (2*var(--border-size)));
          background-repeat:no-repeat;background-position:50% 50%;
          mask:linear-gradient(transparent,transparent),linear-gradient(white,white);
          mask-clip:padding-box,border-box;mask-composite:intersect;
        }
        [data-glow]::before{
          background-image:radial-gradient(
            calc(var(--spotlight-size)*0.75) calc(var(--spotlight-size)*0.75) at
            calc(var(--x,0)*1px) calc(var(--y,0)*1px),
            hsl(var(--hue,210) calc(var(--saturation,100)*1%) calc(var(--lightness,50)*1%)/var(--border-spot-opacity,1)),transparent 100%
          );filter:brightness(2);
        }
        [data-glow]::after{
          background-image:radial-gradient(
            calc(var(--spotlight-size)*0.5) calc(var(--spotlight-size)*0.5) at
            calc(var(--x,0)*1px) calc(var(--y,0)*1px),
            hsl(0 100% 100%/var(--border-light-opacity,1)),transparent 100%
          );
        }
        [data-glow] [data-glow]{
          position:absolute;inset:0;will-change:filter;opacity:var(--outer,1);
          border-radius:calc(var(--radius)*1px);border-width:calc(var(--border-size)*20);
          filter:blur(calc(var(--border-size)*10));background:none;pointer-events:none;border:none;
        }
        [data-glow]>[data-glow]::before{inset:-10px;border-width:10px;}
      `}</style>
      <div
        ref={cardRef}
        data-glow
        className={cn('relative rounded-xl p-4 backdrop-blur-sm', className)}
        style={{
          '--base': base,
          '--spread': spread,
          '--radius': '12',
          '--border': '2',
          '--backdrop': 'hsl(0 0% 10% / 0.8)',
          '--backup-border': 'var(--backdrop)',
          '--size': '200',
          '--outer': '1',
          '--border-size': 'calc(var(--border, 2) * 1px)',
          '--spotlight-size': 'calc(var(--size, 150) * 1px)',
          '--hue': 'calc(var(--base) + (var(--xp, 0) * var(--spread, 0)))',
          backgroundImage: `radial-gradient(
            var(--spotlight-size) var(--spotlight-size) at
            calc(var(--x, 0) * 1px) calc(var(--y, 0) * 1px),
            hsl(var(--hue, 210) 100% 70% / 0.08), transparent
          )`,
          backgroundColor: 'var(--backdrop)',
          backgroundSize: 'calc(100% + (2 * var(--border-size))) calc(100% + (2 * var(--border-size)))',
          backgroundPosition: '50% 50%',
          backgroundAttachment: 'fixed',
          border: 'var(--border-size) solid var(--backup-border)',
        } as React.CSSProperties}
      >
        <div data-glow />
        {children}
      </div>
    </>
  )
}
