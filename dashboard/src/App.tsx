import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Activity, BrainCircuit, CircleAlert, Download, ExternalLink, FileText, FlaskConical, Network, Play, RefreshCw, RadioTower, Workflow } from "lucide-react"
import { AgentGraph } from "@/components/AgentGraph"
import { EventTimeline } from "@/components/EventTimeline"
import { MetricTile } from "@/components/MetricTile"
import { RunSummary } from "@/components/RunSummary"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { DashboardEvent, DashboardState, GraphNode } from "@/types"
import { cn } from "@/lib/utils"

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? `${window.location.protocol}//${window.location.hostname}:18080`
const EVENT_PLAYBACK_INTERVAL_MS = 200
const EVENT_PULSE_DURATION_MS = 850
const GRAPH_WIDTH = 1438
const GRAPH_HEIGHT = 874

type StreamStatus = "connecting" | "connected" | "reconnecting" | "off"
type DemoAction = "local-p1" | "temporal-p1" | "suite" | "llm" | "full-demo" | null

type ExecutedNodeStep = {
  id: string
  sequence: number
  event_id: string
  event_type: string
  node_id: string
  occurred_at: string
  source: string
}

function fmtPercent(value?: number) {
  return `${Math.round((value ?? 0) * 100)}%`
}

function fmtScore(value?: number) {
  return (value ?? 0).toFixed(2)
}

function statusBadge(status?: string) {
  if (status === "active") return "warning"
  if (status === "online" || status === "ok" || status === "connected") return "success"
  if (status === "degraded" || status === "reconnecting") return "destructive"
  return "standby"
}

function connectionLabel(data: DashboardState | null) {
  if (!data) return "Waiting"
  const generated = new Date(data.generated_at)
  return Number.isNaN(generated.getTime()) ? "Live" : generated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

function mergeEvent(events: DashboardEvent[], event: DashboardEvent) {
  const deduped = events.filter((item) => item.event_id !== event.event_id)
  return [...deduped, event].slice(-30)
}

async function downloadReport(url: string, filename: string) {
  const response = await fetch(url, { headers: { Accept: "application/json" } })
  if (!response.ok) throw new Error(`report download ${response.status}`)
  const blob = await response.blob()
  const objectUrl = window.URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.URL.revokeObjectURL(objectUrl)
}

export default function App() {
  const [data, setData] = useState<DashboardState | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [loading, setLoading] = useState(true)
  const [runningAction, setRunningAction] = useState<DemoAction>(null)
  const [streamEnabled, setStreamEnabled] = useState(true)
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting")
  const [lastLiveEvent, setLastLiveEvent] = useState<DashboardEvent | null>(null)
  const [pulseNodeIds, setPulseNodeIds] = useState<string[]>([])
  const [pulseEdgeIds, setPulseEdgeIds] = useState<string[]>([])
  const [executedNodeSteps, setExecutedNodeSteps] = useState<ExecutedNodeStep[]>([])
  const [executedNodeTotal, setExecutedNodeTotal] = useState(0)
  const [graphPanelHeight, setGraphPanelHeight] = useState(360)
  const [error, setError] = useState<string | null>(null)
  const graphColumnRef = useRef<HTMLDivElement | null>(null)
  const pulseTimerRef = useRef<number | null>(null)
  const playbackTimerRef = useRef<number | null>(null)
  const playbackActiveRef = useRef(false)
  const eventQueueRef = useRef<DashboardEvent[]>([])
  const executedStepSequenceRef = useRef(0)
  const initialReplayUpperBoundRef = useRef<number | null>(null)

  const applyState = useCallback((nextData: DashboardState) => {
    setData(nextData)
    setSelectedNode((current) => {
      if (current && nextData.graph.nodes.some((node) => node.id === current.id)) {
        return nextData.graph.nodes.find((node) => node.id === current.id) ?? current
      }
      return nextData.graph.nodes.find((node) => node.id === "langgraph") ?? nextData.graph.nodes[0] ?? null
    })
    setError(null)
    setLoading(false)
  }, [])

  const fetchState = useCallback(async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/dashboard/state`, { headers: { Accept: "application/json" } })
      if (!response.ok) throw new Error(`dashboard state ${response.status}`)
      applyState((await response.json()) as DashboardState)
    } catch (err) {
      setError(err instanceof Error ? err.message : "dashboard state request failed")
      setLoading(false)
    }
  }, [applyState])

  const showGraphPulse = useCallback((event: DashboardEvent) => {
    const nodeIds = event.pulse_node_ids ?? []
    setLastLiveEvent(event)
    setPulseNodeIds(nodeIds)
    setPulseEdgeIds(event.pulse_edge_ids ?? [])
    if (nodeIds.length > 0) {
      const sequenceStart = executedStepSequenceRef.current
      const steps = nodeIds.map((nodeId, index) => ({
        id: `${event.event_id}:${nodeId}:${index}`,
        sequence: sequenceStart + index + 1,
        event_id: event.event_id,
        event_type: event.event_type,
        node_id: nodeId,
        occurred_at: event.occurred_at,
        source: event.source,
      }))
      executedStepSequenceRef.current = sequenceStart + steps.length
      setExecutedNodeTotal(executedStepSequenceRef.current)
      setExecutedNodeSteps((current) => [...steps, ...current].slice(0, 240))
    }
    if (pulseTimerRef.current) window.clearTimeout(pulseTimerRef.current)
    pulseTimerRef.current = window.setTimeout(() => {
      setPulseNodeIds([])
      setPulseEdgeIds([])
    }, EVENT_PULSE_DURATION_MS)
  }, [])

  const playQueuedEvent = useCallback(() => {
    const event = eventQueueRef.current.shift()
    if (!event) {
      playbackActiveRef.current = false
      playbackTimerRef.current = null
      return
    }
    showGraphPulse(event)
    playbackTimerRef.current = window.setTimeout(playQueuedEvent, EVENT_PLAYBACK_INTERVAL_MS)
  }, [showGraphPulse])

  const enqueueGraphPulse = useCallback((event: DashboardEvent) => {
    eventQueueRef.current = [...eventQueueRef.current, event].slice(-300)
    if (!playbackActiveRef.current) {
      playbackActiveRef.current = true
      playQueuedEvent()
    }
  }, [playQueuedEvent])

  const resetGraphPlayback = useCallback(() => {
    eventQueueRef.current = []
    playbackActiveRef.current = false
    if (playbackTimerRef.current) {
      window.clearTimeout(playbackTimerRef.current)
      playbackTimerRef.current = null
    }
    if (pulseTimerRef.current) {
      window.clearTimeout(pulseTimerRef.current)
      pulseTimerRef.current = null
    }
    setPulseNodeIds([])
    setPulseEdgeIds([])
  }, [])

  useEffect(() => {
    void fetchState()
    return () => {
      resetGraphPlayback()
    }
  }, [fetchState, resetGraphPlayback])

  useEffect(() => {
    const element = graphColumnRef.current
    if (!element) return
    const updateHeight = () => {
      const width = element.clientWidth
      const nextHeight = width > 0 ? Math.ceil(GRAPH_HEIGHT * Math.min(1, width / GRAPH_WIDTH)) : 360
      setGraphPanelHeight(nextHeight)
    }
    updateHeight()
    const observer = new ResizeObserver(updateHeight)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!streamEnabled) {
      setStreamStatus("off")
      resetGraphPlayback()
      const timer = window.setInterval(() => void fetchState(), data?.refresh_interval_ms ?? 3000)
      return () => window.clearInterval(timer)
    }

    setStreamStatus("connecting")
    const source = new EventSource(`${apiBaseUrl}/dashboard/events`)

    source.onopen = () => {
      setStreamStatus("connected")
      setError(null)
    }
    source.onerror = () => {
      setStreamStatus("reconnecting")
    }
    source.addEventListener("state", (message) => {
      const state = JSON.parse((message as MessageEvent<string>).data) as DashboardState
      if (initialReplayUpperBoundRef.current === null) {
        initialReplayUpperBoundRef.current = state.latest_stream_id ?? 0
      }
      applyState(state)
    })
    source.addEventListener("dah_event", (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as DashboardEvent
      const streamId = Number(event.stream_id ?? (message as MessageEvent<string>).lastEventId ?? 0)
      const initialReplayUpperBound = initialReplayUpperBoundRef.current
      const isInitialReplay = initialReplayUpperBound !== null && streamId > 0 && streamId <= initialReplayUpperBound
      if (!isInitialReplay) enqueueGraphPulse(event)
      setData((current) => current ? { ...current, recent_events: mergeEvent(current.recent_events, event) } : current)
    })
    source.addEventListener("heartbeat", () => {
      setStreamStatus("connected")
    })

    return () => source.close()
  }, [applyState, data?.refresh_interval_ms, enqueueGraphPulse, fetchState, resetGraphPlayback, streamEnabled])

  const runDemoAction = async (action: Exclude<DemoAction, null>) => {
    setRunningAction(action)
    setError(null)
    executedStepSequenceRef.current = 0
    setExecutedNodeTotal(0)
    setExecutedNodeSteps([])
    try {
      const sessionId = `session-dashboard-${action}-${Date.now()}`
      let endpoint = "/scenarios/run"
      let body: Record<string, unknown> = {
        mission_id: "mission-dashboard",
        session_id: sessionId,
        attack_type: "selective_message_drop",
        duration_seconds: 30,
        use_llm_advisory: true,
      }
      if (action === "temporal-p1") {
        endpoint = "/temporal/scenarios/run"
      } else if (action === "suite") {
        endpoint = "/experiments/run-suite"
        body = { runs_per_group: 1, duration_seconds: 30, session_id_prefix: `session-dashboard-suite-${Date.now()}` }
      } else if (action === "llm") {
        endpoint = "/llm/plan"
        body = {
          incident: {
            incident_id: `inc-dashboard-${Date.now()}`,
            classification: "UNCERTAIN",
            planned_defense_actions: ["increase_monitoring_level", "mark_telemetry_untrusted"],
          },
          allow_fallback: true,
        }
      } else if (action === "full-demo") {
        endpoint = "/demo/run-full"
        body = {}
      }
      const response = await fetch(`${apiBaseUrl}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!response.ok) throw new Error(`${endpoint} ${response.status}`)
      if (action === "full-demo") {
        const payload = await response.json() as { report_json_url?: string; report_filename?: string; primary_run_id?: string }
        if (payload.report_json_url) {
          await downloadReport(
            payload.report_json_url,
            payload.report_filename ?? `dah-agent-full-demo-${payload.primary_run_id ?? Date.now()}.json`,
          )
        }
      }
      await fetchState()
    } catch (err) {
      setError(err instanceof Error ? err.message : "demo action failed")
    } finally {
      setRunningAction(null)
    }
  }

  const metricTiles = useMemo(() => {
    const metrics = data?.metrics ?? {}
    return [
      { label: "Total Runs", value: metrics.dah_total_runs ?? 0, hint: "SQLite evidence ledger" },
      { label: "Detection Rate", value: fmtPercent(metrics.dah_attack_detection_rate), hint: "Red/Blue closed loop" },
      { label: "Recovery Success", value: fmtPercent(metrics.dah_recovery_success_rate), hint: "Blue recovery verified" },
      { label: "Average Score", value: fmtScore(metrics.dah_average_total_score), hint: "Independent judge" },
      { label: "Safe Stop Rate", value: fmtPercent(metrics.dah_safe_stop_rate), hint: "UGV invariant" },
      { label: "Trace Nodes", value: data?.latest_run?.agent_graph.trace.length ?? 0, hint: data?.latest_run?.agent_graph.framework ?? "LangGraph" },
    ]
  }, [data])

  const nodeLabelById = useMemo(() => {
    return new Map((data?.graph.nodes ?? []).map((node) => [node.id, node.label]))
  }, [data?.graph.nodes])

  const nodeKindById = useMemo(() => {
    return new Map((data?.graph.nodes ?? []).map((node) => [node.id, node.kind]))
  }, [data?.graph.nodes])

  const health = data?.health ?? {}
  const temporalStatus = health.temporal?.ok ? "online" : health.temporal?.enabled ? "degraded" : "standby"
  const activeEdges = data?.graph.edges.filter((edge) => edge.active).length ?? 0
  const activeNodes = data?.graph.nodes.filter((node) => node.status === "active").length ?? 0

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border/80 bg-card/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1660px] flex-col gap-3 px-4 py-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Network className="h-5 w-5 text-primary" />
              <h1 className="truncate text-xl font-semibold tracking-normal">DAH Agent Live Operations</h1>
              <Badge variant={statusBadge(health.status)}>API {health.status ?? "pending"}</Badge>
              <Badge variant={statusBadge(streamStatus)}>Stream {streamStatus}</Badge>
              <Badge variant={statusBadge(temporalStatus)}>Temporal {temporalStatus}</Badge>
              <Badge variant={statusBadge(health.redis_streams?.ok ? "online" : "standby")}>Redis {health.redis_streams?.ok ? "online" : "standby"}</Badge>
              <Badge variant={statusBadge(health.openai?.configured ? "online" : "standby")}>LLM {health.openai?.configured ? "configured" : "fallback"}</Badge>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
              <span>{apiBaseUrl} / refreshed {connectionLabel(data)}</span>
              <span>last event {lastLiveEvent?.event_type ?? "waiting"}</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => setStreamEnabled((value) => !value)}>
              <RadioTower className="h-4 w-4" />
              {streamEnabled ? "Stream On" : "Stream Off"}
            </Button>
            <Button variant="outline" onClick={() => void fetchState()} disabled={loading}>
              <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              Refresh
            </Button>
            <Button onClick={() => void runDemoAction("full-demo")} disabled={runningAction !== null}>
              <Download className="h-4 w-4" />
              {runningAction === "full-demo" ? "Running" : "Full Demo"}
            </Button>
            <Button variant="outline" onClick={() => void runDemoAction("local-p1")} disabled={runningAction !== null}>
              <Play className="h-4 w-4" />
              {runningAction === "local-p1" ? "Running" : "Local P1"}
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1660px] px-4 py-4">
        {error ? (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive-foreground">
            <CircleAlert className="h-4 w-4" />
            {error}
          </div>
        ) : null}

        <section className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
          {metricTiles.map((metric, index) => (
            <MetricTile key={metric.label} index={index} {...metric} />
          ))}
        </section>

        <section className="mt-4">
          <Card className="border-border/80 bg-card/90">
            <CardContent className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
                <Activity className="h-4 w-4 text-primary" />
                <span className="truncate">Demo Controls</span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" onClick={() => void runDemoAction("full-demo")} disabled={runningAction !== null}>
                  <Download className="h-4 w-4" />
                  {runningAction === "full-demo" ? "Running" : "Full Demo + Report"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => void runDemoAction("local-p1")} disabled={runningAction !== null}>
                  <Play className="h-4 w-4" />
                  {runningAction === "local-p1" ? "Running" : "Local P1"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => void runDemoAction("temporal-p1")} disabled={runningAction !== null}>
                  <Workflow className="h-4 w-4" />
                  {runningAction === "temporal-p1" ? "Running" : "Temporal P1"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => void runDemoAction("suite")} disabled={runningAction !== null}>
                  <FlaskConical className="h-4 w-4" />
                  {runningAction === "suite" ? "Running" : "E0-E5 Suite"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => void runDemoAction("llm")} disabled={runningAction !== null}>
                  <BrainCircuit className="h-4 w-4" />
                  {runningAction === "llm" ? "Running" : "LLM Advisory"}
                </Button>
                {data?.latest_run ? (
                  <Button size="sm" variant="outline" asChild>
                    <a href={`${apiBaseUrl}/reports/${data.latest_run.run_id}.json`} target="_blank" rel="noreferrer">
                      <FileText className="h-4 w-4" />
                      Latest Report
                    </a>
                  </Button>
                ) : null}
              </div>
            </CardContent>
          </Card>
        </section>

        <section className="mt-4">
          <Card className="border-border/80 bg-card/90">
            <CardHeader className="flex-row items-center justify-between gap-3 space-y-0 pb-3">
              <div>
                <CardTitle>Agent Execution Graph</CardTitle>
                <div className="mt-1 text-xs text-muted-foreground">
                  {activeNodes} active components / {activeEdges} active links / {pulseNodeIds.length} live pulses
                </div>
              </div>
              <Badge variant="outline">{data?.graph.nodes.length ?? 0} components</Badge>
            </CardHeader>
            <CardContent>
              <div className="grid min-h-0 items-start gap-3 xl:grid-cols-[minmax(0,1fr)_220px]">
                <div ref={graphColumnRef} className="min-w-0">
                  <AgentGraph
                    nodes={data?.graph.nodes ?? []}
                    edges={data?.graph.edges ?? []}
                    selectedNodeId={selectedNode?.id}
                    pulseNodeIds={pulseNodeIds}
                    pulseEdgeIds={pulseEdgeIds}
                    onSelectNode={setSelectedNode}
                  />
                </div>
                <div
                  className="flex min-h-0 flex-col overflow-hidden rounded-md border border-border/80 bg-background/80"
                  style={{ height: graphPanelHeight, maxHeight: graphPanelHeight }}
                >
                  <div className="flex items-center justify-between gap-2 border-b border-border/80 px-3 py-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">Executed Nodes</div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">200ms visual playback</div>
                    </div>
                    <Badge variant="outline" className="shrink-0">{executedNodeTotal}</Badge>
                  </div>
                  <ScrollArea className="min-h-0 flex-1 overflow-hidden pr-2">
                    <div className="space-y-1.5 p-2">
                      {executedNodeSteps.map((step, index) => {
                        const live = step.event_id === lastLiveEvent?.event_id
                        return (
                          <button
                            key={step.id}
                            type="button"
                            onClick={() => {
                              const node = data?.graph.nodes.find((item) => item.id === step.node_id)
                              if (node) setSelectedNode(node)
                            }}
                            className={cn(
                              "flex w-full items-start gap-2 rounded-md border px-2 py-2 text-left transition hover:border-primary",
                              live ? "live-event border-cyan-300/70 bg-cyan-950/35" : "border-border/70 bg-card/70",
                            )}
                          >
                            <span className={cn(
                              "mt-0.5 flex h-5 w-7 shrink-0 items-center justify-center rounded-sm text-[10px] tabular-nums",
                              live ? "bg-cyan-300 text-slate-950" : "bg-muted text-muted-foreground",
                            )}>
                              {step.sequence}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-xs font-semibold">{nodeLabelById.get(step.node_id) ?? step.node_id}</span>
                              <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{step.event_type}</span>
                              <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">{nodeKindById.get(step.node_id) ?? step.source}</span>
                            </span>
                          </button>
                        )
                      })}
                      {executedNodeSteps.length === 0 ? (
                        <div className="rounded-md border border-dashed border-border/80 p-3 text-xs text-muted-foreground">
                          Run a scenario to populate this path.
                        </div>
                      ) : null}
                    </div>
                  </ScrollArea>
                </div>
              </div>
            </CardContent>
          </Card>
        </section>

        <section className="mt-4 grid gap-4 xl:grid-cols-[430px_360px_1fr]">
          <RunSummary run={data?.latest_run ?? null} />
          <Card>
            <CardHeader className="pb-3">
              <CardTitle>Selected Component</CardTitle>
            </CardHeader>
            <CardContent>
              {selectedNode ? (
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">{selectedNode.label}</div>
                      <div className="mt-1 text-xs text-muted-foreground">{selectedNode.layer} / {selectedNode.kind}</div>
                    </div>
                    <Badge variant={statusBadge(selectedNode.status)}>{selectedNode.status}</Badge>
                  </div>
                  <Separator className="my-3" />
                  <p className="text-sm leading-6 text-muted-foreground">{selectedNode.description}</p>
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">No component selected</div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle>Live Event Stream</CardTitle>
            </CardHeader>
            <CardContent>
              <EventTimeline events={data?.recent_events ?? []} liveEventId={lastLiveEvent?.event_id} />
            </CardContent>
          </Card>
        </section>

        <section className="mt-4">
          <Tabs defaultValue="trace">
            <TabsList>
              <TabsTrigger value="trace">Graph Trace</TabsTrigger>
              <TabsTrigger value="runtime">Runtime</TabsTrigger>
              <TabsTrigger value="coverage">Coverage</TabsTrigger>
            </TabsList>
            <TabsContent value="trace">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle>LangGraph Trace</CardTitle>
                </CardHeader>
                <CardContent>
                  <ScrollArea className="h-[220px]">
                    <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                      {(data?.latest_run?.agent_graph.trace ?? []).map((item, index) => (
                        <div key={`${item.node}-${index}`} className="rounded-md border bg-card px-3 py-2">
                          <div className="text-xs text-muted-foreground">#{index + 1}</div>
                          <div className="mt-1 truncate text-sm font-semibold">{item.node}</div>
                          <div className="mt-1 truncate text-xs text-muted-foreground">{Object.keys(item.detail ?? {}).join(", ") || "state"}</div>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="runtime">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle>Runtime Links</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-2 md:grid-cols-3">
                  {Object.entries(data?.links ?? {}).map(([label, href]) => (
                    <a key={label} href={href} target="_blank" rel="noreferrer" className="flex items-center justify-between rounded-md border bg-card px-3 py-2 text-sm hover:border-primary">
                      <span className="truncate">{label.replaceAll("_", " ")}</span>
                      <ExternalLink className="h-4 w-4 shrink-0 text-muted-foreground" />
                    </a>
                  ))}
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="coverage">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle>Report Coverage Map</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-2 lg:grid-cols-2">
                    {(data?.coverage.items ?? []).map((item) => (
                      <div key={item.requirement} className="rounded-md border bg-card px-3 py-2">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-medium">{item.requirement}</div>
                            <div className="mt-1 truncate text-xs text-muted-foreground">{item.evidence.join(" / ")}</div>
                          </div>
                          <Badge variant={item.status.includes("implemented") ? "success" : "standby"}>{item.status}</Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </section>
      </main>
    </div>
  )
}
