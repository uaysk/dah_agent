import { Clock3, Zap } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { DashboardEvent } from "@/types"
import { cn } from "@/lib/utils"

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

export function EventTimeline({ events, liveEventId }: { events: DashboardEvent[]; liveEventId?: string }) {
  return (
    <ScrollArea className="h-[360px] pr-3">
      <div className="space-y-2">
        {events.slice().reverse().map((event) => {
          const live = event.event_id === liveEventId
          return (
            <div key={event.event_id} className={cn("rounded-md border bg-card p-3 transition", live && "live-event border-cyan-300/70 bg-cyan-950/30")}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-2">
                    {live ? <Zap className="h-3.5 w-3.5 shrink-0 text-cyan-200" /> : null}
                    <div className="truncate text-sm font-medium">{event.event_type}</div>
                  </div>
                  <div className="mt-1 truncate text-xs text-muted-foreground">{event.source}</div>
                </div>
                <Badge variant={live ? "success" : "outline"} className="shrink-0 gap-1">
                  <Clock3 className="h-3 w-3" />
                  {formatTime(event.occurred_at)}
                </Badge>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-muted-foreground">
                {Object.entries(event.payload_summary).slice(0, 6).map(([key, value]) => (
                  <div key={key} className="truncate rounded bg-muted px-2 py-1">
                    <span className="font-medium text-foreground/80">{key}</span>: {String(value)}
                  </div>
                ))}
              </div>
            </div>
          )
        })}
        {events.length === 0 ? <div className="rounded-md border p-4 text-sm text-muted-foreground">No events</div> : null}
      </div>
    </ScrollArea>
  )
}
