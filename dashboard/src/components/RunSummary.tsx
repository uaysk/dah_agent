import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { LatestRun } from "@/types"

function cell(label: string, value: unknown) {
  return (
    <div className="min-w-0 border-b border-zinc-800 py-2 last:border-b-0">
      <div className="text-[10px] font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold tabular-nums">{String(value ?? "-")}</div>
    </div>
  )
}

export function RunSummary({ run }: { run: LatestRun | null }) {
  if (!run) {
    return (
      <Card>
        <CardHeader className="border-b"><CardTitle>Latest Run</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">No run recorded</CardContent>
      </Card>
    )
  }
  return (
    <Card>
      <CardHeader className="border-b pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="truncate">Latest Run</CardTitle>
            <div className="mt-1 truncate text-xs text-muted-foreground">{run.run_id} / {run.incident_id}</div>
          </div>
          <Badge variant="success">{run.status}</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-x-4 pt-2">
        <div>
          {cell("Attack Type", run.request.attack_type)}
          {cell("Total Score", run.judge.total_score)}
          {cell("UGV State", run.mission.ugv_final_state)}
          {cell("Graph", run.agent_graph.framework)}
          {cell("OpenAI Used", run.llm_plan?.openai_used)}
        </div>
        <div>
          {cell("Classification", run.classification.classification)}
          {cell("Final Verdict", run.judge.final_verdict)}
          {cell("Safe Stop", run.mission.safe_stop_triggered)}
          {cell("Trace Nodes", run.agent_graph.trace.length)}
          {cell("LLM Source", run.llm_plan?.source)}
        </div>
      </CardContent>
    </Card>
  )
}
