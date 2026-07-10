import { Activity, Gauge, GitBranch, ShieldCheck, Timer, TrendingUp } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"

const icons = [Activity, ShieldCheck, Gauge, TrendingUp, Timer, GitBranch]

interface MetricTileProps {
  label: string
  value: string | number
  hint?: string
  index: number
}

export function MetricTile({ label, value, hint, index }: MetricTileProps) {
  const Icon = icons[index % icons.length]
  return (
    <Card className="rounded-none border-0 bg-card">
      <CardContent className="flex h-[96px] flex-col justify-between p-3.5">
        <div className="flex items-center justify-between gap-2">
          <div className="truncate text-[11px] font-medium uppercase text-muted-foreground">{label}</div>
          <Icon className="h-3.5 w-3.5 shrink-0 text-zinc-500" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-2xl font-semibold tabular-nums leading-none">{value}</div>
          {hint ? <div className="mt-1.5 truncate text-[10px] text-muted-foreground">{hint}</div> : null}
        </div>
      </CardContent>
    </Card>
  )
}
