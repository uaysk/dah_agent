export type ComponentStatus = "online" | "active" | "standby" | "degraded"

export interface GraphNode {
  id: string
  label: string
  kind: string
  layer: string
  description: string
  status: ComponentStatus
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  label: string
  active: boolean
  event_types: string[]
}

export interface DashboardEvent {
  stream_id?: number
  event_id: string
  run_id?: string
  incident_id?: string
  event_type: string
  source: string
  occurred_at: string
  payload_summary: Record<string, unknown>
  pulse_node_ids?: string[]
  pulse_edge_ids?: string[]
}

export interface LatestRun {
  run_id: string
  incident_id: string
  created_at: string
  status: string
  request: Record<string, unknown>
  classification: Record<string, unknown>
  judge: Record<string, unknown>
  mission: Record<string, unknown>
  agent_graph: {
    framework?: string
    purpose?: string
    durable_state_owner?: string
    decisions: Record<string, unknown>
    trace: Array<{ node: string; detail?: Record<string, unknown> }>
  }
  llm_plan?: {
    openai_used: boolean
    source: string
    model?: string
    applied_to_execution: boolean
    plan: Record<string, unknown>
    recommended_actions_allowed?: string[]
    recommended_actions_denied?: string[]
    error?: string | null
  } | null
}

export interface DashboardState {
  generated_at: string
  refresh_interval_ms: number
  stream_url?: string
  latest_stream_id?: number
  health: Record<string, any>
  graph: { nodes: GraphNode[]; edges: GraphEdge[] }
  metrics: Record<string, number>
  latest_run: LatestRun | null
  recent_events: DashboardEvent[]
  coverage: { items: Array<{ requirement: string; status: string; evidence: string[] }> }
  links: Record<string, string>
}
