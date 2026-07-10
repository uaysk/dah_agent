import { useEffect, useMemo, useRef, useState } from "react"
import {
  Activity,
  Bot,
  Braces,
  CheckCircle2,
  CircuitBoard,
  Database,
  FileJson,
  Gauge,
  GitBranch,
  LockKeyhole,
  Network,
  Radio,
  RefreshCcw,
  Shield,
  TerminalSquare,
  Waypoints,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { ComponentStatus, GraphEdge, GraphNode } from "@/types"
import { cn } from "@/lib/utils"

const NODE_WIDTH = 184
const NODE_HEIGHT = 74
const GRAPH_WIDTH = 1438
const GRAPH_HEIGHT = 874

const positions: Record<string, { x: number; y: number }> = {
  "external-api": { x: 34, y: 52 },
  fastapi: { x: 262, y: 52 },
  temporal: { x: 500, y: 28 },
  langgraph: { x: 500, y: 152 },
  "red-agent": { x: 742, y: 70 },
  "tool-registry": { x: 980, y: 38 },
  "policy-gateway": { x: 980, y: 146 },
  "tool-executor": { x: 1214, y: 146 },
  simulator: { x: 1214, y: 306 },
  "truth-state": { x: 980, y: 306 },
  verifier: { x: 742, y: 306 },
  "blue-agent": { x: 500, y: 306 },
  recovery: { x: 500, y: 450 },
  judge: { x: 742, y: 450 },
  "evidence-ledger": { x: 980, y: 450 },
  redis: { x: 1214, y: 450 },
  openai: { x: 262, y: 192 },
  reports: { x: 980, y: 594 },
  metrics: { x: 1214, y: 594 },
  grafana: { x: 1214, y: 710 },
}

const iconMap: Record<string, typeof Activity> = {
  "external-api": TerminalSquare,
  fastapi: Radio,
  temporal: RefreshCcw,
  langgraph: GitBranch,
  "red-agent": Bot,
  "tool-registry": Braces,
  "policy-gateway": LockKeyhole,
  "tool-executor": CircuitBoard,
  simulator: Waypoints,
  "truth-state": Database,
  verifier: CheckCircle2,
  "blue-agent": Shield,
  recovery: RefreshCcw,
  judge: Gauge,
  "evidence-ledger": Database,
  redis: Network,
  openai: Bot,
  reports: FileJson,
  metrics: Activity,
  grafana: Gauge,
}

const layerBands = [
  { label: "Interface", x: 12, y: 16, w: 456, h: 122, className: "border-zinc-700 bg-zinc-950/80" },
  { label: "Orchestration / Agent Reasoning", x: 478, y: 16, w: 458, h: 238, className: "border-zinc-600 bg-zinc-900/80" },
  { label: "Policy Control", x: 948, y: 16, w: 468, h: 238, className: "border-zinc-500 bg-zinc-950/90" },
  { label: "Mission Simulation", x: 948, y: 284, w: 468, h: 130, className: "border-zinc-600 bg-zinc-900/65" },
  { label: "Evidence / Recovery / Judge", x: 478, y: 284, w: 458, h: 256, className: "border-zinc-600 bg-zinc-900/75" },
  { label: "Storage / Observability", x: 948, y: 428, w: 468, h: 430, className: "border-zinc-700 bg-zinc-950/90" },
]

function statusVariant(status: ComponentStatus) {
  if (status === "active") return "warning"
  if (status === "online") return "success"
  if (status === "degraded") return "destructive"
  return "standby"
}

function edgePath(edge: GraphEdge) {
  const source = positions[edge.source]
  const target = positions[edge.target]
  if (!source || !target) return ""
  const startX = source.x + NODE_WIDTH
  const startY = source.y + NODE_HEIGHT / 2
  const endX = target.x
  const endY = target.y + NODE_HEIGHT / 2
  const dx = Math.max(70, Math.abs(endX - startX) * 0.45)
  if (edge.source === "recovery" && edge.target === "tool-executor") {
    return `M ${source.x + NODE_WIDTH / 2} ${source.y} C ${source.x + 40} ${source.y - 72}, ${target.x + NODE_WIDTH / 2} ${target.y - 72}, ${target.x + NODE_WIDTH / 2} ${target.y}`
  }
  if (edge.source === "evidence-ledger" || edge.source === "metrics") {
    const verticalStartX = source.x + NODE_WIDTH / 2
    const verticalEndX = target.x + NODE_WIDTH / 2
    return `M ${verticalStartX} ${source.y + NODE_HEIGHT} C ${verticalStartX} ${source.y + 120}, ${verticalEndX} ${target.y - 44}, ${verticalEndX} ${target.y}`
  }
  if (edge.source === "fastapi" && edge.target === "openai") {
    return `M ${source.x + NODE_WIDTH / 2} ${source.y + NODE_HEIGHT} C ${source.x + 120} ${source.y + 110}, ${target.x + 86} ${target.y - 52}, ${target.x + NODE_WIDTH / 2} ${target.y}`
  }
  return `M ${startX} ${startY} C ${startX + dx} ${startY}, ${endX - dx} ${endY}, ${endX} ${endY}`
}

function NodeCard({
  node,
  selected,
  live,
  onSelect,
}: {
  node: GraphNode
  selected: boolean
  live: boolean
  onSelect: (node: GraphNode) => void
}) {
  const Icon = iconMap[node.id] ?? Activity
  const displayLabel = node.id === "simulator" ? "Mission Simulator" : node.label
  const displayKind = node.id === "simulator" ? "UAV · UGV · GCS · relay" : node.kind
  return (
    <button
      type="button"
      onClick={() => onSelect(node)}
      title={`${node.label}: ${node.description}`}
      className={cn(
        "absolute z-30 flex h-[74px] w-[184px] flex-col items-start overflow-hidden rounded-md border border-zinc-700 bg-zinc-950 p-2.5 text-left transition-colors",
        "hover:border-zinc-300 hover:bg-zinc-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950",
        selected && "border-zinc-100 ring-1 ring-zinc-100",
        node.status === "active" && "border-zinc-400 bg-zinc-800",
        node.status === "degraded" && "border-zinc-300 bg-zinc-700",
        live && "node-live border-zinc-100 bg-zinc-100 text-zinc-950",
      )}
      style={{ left: positions[node.id]?.x ?? 0, top: positions[node.id]?.y ?? 0 }}
    >
      <div className="flex min-h-8 w-full items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900 text-zinc-300", live && "border-zinc-400 bg-zinc-200 text-zinc-950")}>
            <Icon className="h-4 w-4" />
          </span>
          <span className={cn("node-label line-clamp-2 whitespace-normal text-xs font-semibold leading-4 [overflow-wrap:anywhere]", live && "text-zinc-950")}>{displayLabel}</span>
        </div>
      </div>
      <div className="mt-1 flex w-full items-center justify-between gap-2">
        <Badge variant={live ? "outline" : statusVariant(node.status)} className={cn("shrink-0 capitalize", live && "border-zinc-400 bg-zinc-100 text-zinc-950 before:bg-zinc-950")}>
          {node.status}
        </Badge>
        <span className={cn("truncate text-[11px] text-muted-foreground", live && "text-zinc-600")}>{displayKind}</span>
      </div>
    </button>
  )
}

export function AgentGraph({
  nodes,
  edges,
  selectedNodeId,
  pulseNodeIds = [],
  pulseEdgeIds = [],
  onSelectNode,
}: {
  nodes: GraphNode[]
  edges: GraphEdge[]
  selectedNodeId?: string
  pulseNodeIds?: string[]
  pulseEdgeIds?: string[]
  onSelectNode: (node: GraphNode) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const updateScale = () => {
      const width = container.clientWidth
      setScale(width > 0 ? Math.min(1, width / GRAPH_WIDTH) : 1)
    }
    updateScale()
    const observer = new ResizeObserver(updateScale)
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  return (
    <div
      ref={containerRef}
      className="overflow-hidden rounded-md border border-zinc-700 bg-zinc-950"
      style={{ height: Math.ceil(GRAPH_HEIGHT * scale) }}
    >
      <div
        className="graph-surface relative h-[874px] w-[1438px] origin-top-left"
        style={{ transform: `scale(${scale})` }}
      >
        {layerBands.map((band) => (
          <div
            key={`${band.label}-band`}
            className={cn("absolute z-0 rounded-md border", band.className)}
            style={{ left: band.x, top: band.y, width: band.w, height: band.h }}
          />
        ))}
        {layerBands.map((band) => (
          <div
            key={`${band.label}-label`}
            className="absolute z-20 flex items-center justify-center rounded-sm border border-zinc-700 bg-zinc-950/95 px-1 text-[10px] font-semibold uppercase text-zinc-400"
            style={{
              left: band.x + 4,
              top: band.y + 10,
              width: 16,
              height: Math.max(56, band.h - 20),
              writingMode: "vertical-rl",
              transform: "rotate(180deg)",
            }}
          >
            {band.label}
          </div>
        ))}
        <svg className="absolute inset-0 z-10 h-full w-full" viewBox="0 0 1438 874" aria-hidden="true">
          <defs>
            <marker id="arrow-live" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#fafafa" />
            </marker>
            <marker id="arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#d4d4d8" />
            </marker>
            <marker id="arrow-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#52525b" />
            </marker>
          </defs>
          {edges.map((edge) => {
            const path = edgePath(edge)
            if (!path || !nodeMap.has(edge.source) || !nodeMap.has(edge.target)) return null
            const live = pulseEdgeIds.includes(edge.id)
            return (
              <g key={edge.id}>
                <path
                  d={path}
                  fill="none"
                  stroke={live ? "#fafafa" : edge.active ? "#d4d4d8" : "#52525b"}
                  strokeWidth={live ? 3.6 : edge.active ? 2.2 : 1.25}
                  strokeDasharray={live ? "9 5" : edge.active ? "10 6" : "4 7"}
                  markerEnd={live ? "url(#arrow-live)" : edge.active ? "url(#arrow-active)" : "url(#arrow-muted)"}
                  className={live ? "live-edge" : edge.active ? "active-edge" : undefined}
                />
              </g>
            )
          })}
        </svg>
        {nodes.map((node) => (
          <NodeCard
            key={node.id}
            node={node}
            selected={selectedNodeId === node.id}
            live={pulseNodeIds.includes(node.id)}
            onSelect={onSelectNode}
          />
        ))}
      </div>
    </div>
  )
}
