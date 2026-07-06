import { Activity, Gauge, ShieldCheck, Timer, TrendingUp } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"

const icons = [Activity, ShieldCheck, Gauge, Timer, TrendingUp]

interface MetricTileProps {
  label: string
  value: string | number
  hint?: string
  index: number
}

export function MetricTile({ label, value, hint, index }: MetricTileProps) {
  const Icon = icons[index % icons.length]
  return (
    <Card className="overflow-hidden">
      <CardContent className="flex h-[88px] items-center gap-3 p-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-xs text-muted-foreground">{label}</div>
          <div className="mt-1 truncate text-xl font-semibold tabular-nums">{value}</div>
          {hint ? <div className="mt-1 truncate text-[11px] text-muted-foreground">{hint}</div> : null}
        </div>
      </CardContent>
    </Card>
  )
}
