from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import Settings, get_settings
from .models import (
    BatchExperimentRequest,
    BatchExperimentResponse,
    ExperimentSuiteRequest,
    ExperimentSuiteResponse,
    FullDemoResponse,
    LlmPlanRequest,
    LlmPlanResponse,
    MissionSimRequest,
    MissionSimResponse,
    ReplayResponse,
    ReportResponse,
    ScenarioRunRequest,
    ScenarioRunResponse,
    TemporalWorkflowResponse,
    ToolExecutionRequest,
    ToolExecutionResponse,
    new_id,
)
from .report_coverage import build_report_coverage
from .runtime import build_runtime
from .temporal_client import TemporalGateway, TemporalUnavailable


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


METRIC_NAMES = [
    "dah_total_runs",
    "dah_attack_detection_rate",
    "dah_recovery_success_rate",
    "dah_average_total_score",
    "dah_average_availability",
    "dah_average_coordinate_gap_seconds",
    "dah_safe_stop_rate",
    "dah_false_positive_rate",
    "dah_false_negative_rate",
    "dah_average_detection_latency_seconds",
    "dah_average_recovery_time_seconds",
    "dah_last_total_score",
]


def parse_time(value: str | float | int | None) -> float:
    if value is None:
        return datetime.now(timezone.utc).timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return datetime.now(timezone.utc).timestamp()


def run_timestamp(row: dict[str, Any]) -> float:
    return parse_time(row.get("created_at"))


def metric_snapshot(rows: list[dict[str, Any]]) -> dict[str, float]:
    count = len(rows)
    if count == 0:
        return {name: 0.0 for name in METRIC_NAMES}
    detected = 0
    recovered = 0
    safe_stop = 0
    false_positive = 0
    false_negative = 0
    attack_count = 0
    non_attack_count = 0
    total_scores: list[float] = []
    availability: list[float] = []
    gaps: list[float] = []
    detection_latencies: list[float] = []
    recovery_times: list[float] = []
    for row in rows:
        response = row["response"]
        request = row.get("request") or {}
        scenario_is_attack = request.get("attack_type") not in {"random_packet_loss", "normal_baseline"}
        if scenario_is_attack:
            attack_count += 1
        else:
            non_attack_count += 1
        classification = response.get("classification", {}).get("classification")
        is_detected = classification in {"ATTACK_SUSPECTED", "ATTACK_CONFIRMED"}
        if is_detected:
            detected += 1
        if (not scenario_is_attack) and is_detected:
            false_positive += 1
        if scenario_is_attack and not is_detected:
            false_negative += 1
        if response.get("recovery_verification", {}).get("success"):
            recovered += 1
        impact = (response.get("impact_verification") or {}).get("result") or {}
        first_anomaly = impact.get("first_anomaly_second")
        threshold = impact.get("detection_threshold_second")
        if is_detected and first_anomaly is not None and threshold is not None:
            detection_latencies.append(float(threshold) - float(first_anomaly))
        summary = (response.get("mission_simulation") or {}).get("summary") or {}
        if summary.get("safe_stop_triggered"):
            safe_stop += 1
        gaps.append(float(summary.get("max_consecutive_coordinate_gap_seconds") or 0))
        recovery_times.append(float((summary.get("safe_stop_second") or request.get("duration_seconds") or 0) - (first_anomaly or 0)))
        verdict = response.get("judge_verdict", {})
        total_scores.append(float(verdict.get("total_score") or 0))
        availability.append(float(verdict.get("availability") or 0))
    return {
        "dah_total_runs": float(count),
        "dah_attack_detection_rate": round(detected / count, 4),
        "dah_recovery_success_rate": round(recovered / count, 4),
        "dah_average_total_score": round(sum(total_scores) / count, 4),
        "dah_average_availability": round(sum(availability) / count, 4),
        "dah_average_coordinate_gap_seconds": round(sum(gaps) / count, 4),
        "dah_safe_stop_rate": round(safe_stop / count, 4),
        "dah_false_positive_rate": round(false_positive / max(non_attack_count, 1), 4),
        "dah_false_negative_rate": round(false_negative / max(attack_count, 1), 4),
        "dah_average_detection_latency_seconds": round(sum(detection_latencies) / max(len(detection_latencies), 1), 4),
        "dah_average_recovery_time_seconds": round(sum(recovery_times) / max(len(recovery_times), 1), 4),
        "dah_last_total_score": total_scores[-1],
    }


def prometheus_vector(metric_name: str, value: float, ts: float) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": metric_name, "job": "dah-agent-poc"},
                    "value": [ts, str(value)],
                }
            ],
        },
    }


def prometheus_matrix(metric_name: str, values: list[list[Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"__name__": metric_name, "job": "dah-agent-poc"},
                    "values": values,
                }
            ],
        },
    }


async def prometheus_post_params(request: Request) -> dict[str, str]:
    params = dict(request.query_params)
    body = (await request.body()).decode()
    if body:
        parsed = parse_qs(body, keep_blank_values=True)
        params.update({key: values[-1] if values else "" for key, values in parsed.items()})
    return params


DASHBOARD_NODES = [
    {"id": "external-api", "label": "External API Test Client", "kind": "entrypoint", "layer": "interface", "description": "External evaluator or operator calling REST APIs."},
    {"id": "fastapi", "label": "FastAPI Gateway", "kind": "api", "layer": "interface", "description": "Public API surface for scenarios, tools, reports, metrics, and dashboard state."},
    {"id": "temporal", "label": "Temporal Workflow", "kind": "workflow", "layer": "orchestration", "description": "Optional durable scenario, batch, and suite workflow orchestration."},
    {"id": "langgraph", "label": "LangGraph Trace Adapter", "kind": "agent_graph", "layer": "agent", "description": "Red/Blue reasoning graph trace; durable state stays in Temporal/local runner."},
    {"id": "red-agent", "label": "Red Agent", "kind": "agent", "layer": "agent", "description": "Builds allowed mock attack plans for P1/P2/P3 and E0/E1 profiles."},
    {"id": "tool-registry", "label": "Tool Registry", "kind": "policy", "layer": "control", "description": "Allowlisted tool contracts, aliases, and minimum risk levels."},
    {"id": "policy-gateway", "label": "Policy Gateway", "kind": "policy", "layer": "control", "description": "Risk, target, and idempotency boundary before any state change."},
    {"id": "tool-executor", "label": "Single Tool Executor", "kind": "executor", "layer": "control", "description": "Only component allowed to apply simulator/tool state changes."},
    {"id": "simulator", "label": "UAV/UGV/GCS/Satellite Simulator", "kind": "simulator", "layer": "simulation", "description": "Closed mock mission environment with Return Link, telemetry, GCS display, and UGV safe stop."},
    {"id": "truth-state", "label": "Truth State API", "kind": "state", "layer": "simulation", "description": "Verifier/Judge baseline for mission and session truth."},
    {"id": "verifier", "label": "Verification Layer", "kind": "verification", "layer": "evidence", "description": "Computes impact and recovery verification from tool results and truth state."},
    {"id": "blue-agent", "label": "Blue Agent", "kind": "agent", "layer": "agent", "description": "Classifies fault vs attack and plans least-impact defense/recovery actions."},
    {"id": "recovery", "label": "Recovery Manager", "kind": "recovery", "layer": "control", "description": "Resynchronization, safe containment, and validated session restore."},
    {"id": "judge", "label": "Independent Judge", "kind": "judge", "layer": "evidence", "description": "Scores attack, defense, availability using truth state and audit evidence."},
    {"id": "evidence-ledger", "label": "Evidence Ledger / SQLite", "kind": "ledger", "layer": "storage", "description": "Authoritative local event, run, evidence, replay, and verdict ledger."},
    {"id": "redis", "label": "Redis Streams", "kind": "stream", "layer": "storage", "description": "Optional homelab event stream copy for sim, attack, defense, tool, judge, replay, and DLQ events."},
    {"id": "openai", "label": "LiteLLM / OpenAI API", "kind": "llm", "layer": "external", "description": "Advisory typed-plan endpoint for uncertain cases; never directly executes tools."},
    {"id": "reports", "label": "Reports & Replay", "kind": "reporting", "layer": "evidence", "description": "JSON/Markdown reports, deterministic replay, and report coverage mapping."},
    {"id": "metrics", "label": "Prometheus Metrics", "kind": "metrics", "layer": "observability", "description": "Prometheus-compatible API used by Grafana and this dashboard."},
    {"id": "grafana", "label": "Grafana", "kind": "dashboard", "layer": "observability", "description": "Provisioned mission metrics dashboard."},
]

DASHBOARD_EDGES = [
    {"source": "external-api", "target": "fastapi", "label": "REST calls", "event_types": ["full_demo_started", "full_demo_completed", "full_demo_failed"]},
    {"source": "fastapi", "target": "temporal", "label": "optional durable workflow", "event_types": ["temporal_workflow_started", "temporal_workflow_completed", "temporal_workflow_failed"]},
    {"source": "fastapi", "target": "langgraph", "label": "scenario request", "event_types": ["langgraph_red_trace", "langgraph_blue_trace", "langgraph_consistency_checked"]},
    {"source": "langgraph", "target": "red-agent", "label": "red reasoning trace", "event_types": ["langgraph_red_trace"]},
    {"source": "red-agent", "target": "tool-registry", "label": "tool contract lookup", "event_types": ["red_plan_created"]},
    {"source": "tool-registry", "target": "policy-gateway", "label": "allowlist/risk", "event_types": ["tool_executed", "tool_denied", "tool_duplicate"]},
    {"source": "policy-gateway", "target": "tool-executor", "label": "approved request", "event_types": ["tool_executed"]},
    {"source": "tool-executor", "target": "simulator", "label": "mock tool effect", "event_types": ["tool_executed"]},
    {"source": "simulator", "target": "truth-state", "label": "mission/session truth", "event_types": ["baseline_ready"]},
    {"source": "truth-state", "target": "verifier", "label": "impact/recovery facts", "event_types": ["impact_verified", "recovery_verified"]},
    {"source": "verifier", "target": "langgraph", "label": "blue evidence trace", "event_types": ["langgraph_blue_trace"]},
    {"source": "langgraph", "target": "blue-agent", "label": "blue reasoning trace", "event_types": ["langgraph_blue_trace"]},
    {"source": "blue-agent", "target": "recovery", "label": "defense plan", "event_types": ["classification_completed", "safe_containment_entered"]},
    {"source": "recovery", "target": "tool-executor", "label": "defense tools", "event_types": ["tool_executed"]},
    {"source": "verifier", "target": "judge", "label": "verified evidence", "event_types": ["judge_verdict"]},
    {"source": "truth-state", "target": "judge", "label": "truth baseline", "event_types": ["judge_verdict"]},
    {"source": "judge", "target": "evidence-ledger", "label": "verdict", "event_types": ["judge_verdict"]},
    {"source": "tool-executor", "target": "evidence-ledger", "label": "audit event", "event_types": ["tool_executed", "tool_denied", "tool_duplicate"]},
    {"source": "evidence-ledger", "target": "redis", "label": "best-effort stream copy", "event_types": []},
    {"source": "fastapi", "target": "openai", "label": "LLM advisory plan", "event_types": ["llm_plan_requested", "llm_plan_completed", "llm_plan_failed"]},
    {"source": "evidence-ledger", "target": "reports", "label": "reports/replay", "event_types": ["full_demo_completed", "report_generated", "replay_completed"]},
    {"source": "evidence-ledger", "target": "metrics", "label": "metric snapshot", "event_types": ["batch_experiment_completed", "experiment_suite_completed"]},
    {"source": "metrics", "target": "grafana", "label": "Prometheus datasource", "event_types": []},
]


def latest_run_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    response = row["response"]
    verdict = response.get("judge_verdict") or {}
    summary = (response.get("mission_simulation") or {}).get("summary") or {}
    graph = response.get("agent_graph") or {}
    return {
        "run_id": row["run_id"],
        "incident_id": row["incident_id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "request": row.get("request") or {},
        "classification": response.get("classification") or {},
        "judge": {
            "attack_score": verdict.get("attack_score"),
            "defense_score": verdict.get("defense_score"),
            "availability": verdict.get("availability"),
            "total_score": verdict.get("total_score"),
            "labels": verdict.get("labels", []),
            "final_verdict": (verdict.get("reason") or {}).get("judge_audit_event", {}).get("final_verdict"),
        },
        "mission": {
            "safe_stop_triggered": summary.get("safe_stop_triggered"),
            "safe_stop_second": summary.get("safe_stop_second"),
            "ugv_final_state": summary.get("ugv_final_state"),
            "max_consecutive_coordinate_gap_seconds": summary.get("max_consecutive_coordinate_gap_seconds"),
            "delivered_reports": summary.get("delivered_reports"),
            "dropped_reports": summary.get("dropped_reports"),
        },
        "agent_graph": {
            "framework": graph.get("framework"),
            "purpose": graph.get("purpose"),
            "durable_state_owner": graph.get("durable_state_owner"),
            "decisions": graph.get("decisions", {}),
            "trace": graph.get("trace", []),
        },
        "llm_plan": response.get("llm_plan"),
    }


def recent_event_payload(events: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "stream_id": event.get("stream_id"),
            "event_id": event.get("event_id"),
            "run_id": event.get("run_id"),
            "incident_id": event.get("incident_id"),
            "event_type": event.get("event_type"),
            "source": event.get("source"),
            "occurred_at": event.get("occurred_at"),
            "payload_summary": summarize_payload(event.get("payload") or {}),
        }
        for event in events[-limit:]
    ]


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ["tool_name", "policy_result", "accepted", "status", "classification", "attack_score", "fault_score", "success", "openai_used", "source", "model", "applied_to_execution", "consistent", "red_tool_match", "blue_actions_match", "workflow_id", "json_sha256", "event_count"]:
        if key in payload:
            summary[key] = payload[key]
    result = payload.get("result")
    if isinstance(result, dict):
        for key in ["command_gap", "max_consecutive_gap_seconds", "target_drop_rate", "fault_profile", "state_converged"]:
            if key in result:
                summary[f"result.{key}"] = result[key]
    if "graph" in payload:
        summary["graph"] = payload.get("graph")
        summary["trace_count"] = len(payload.get("trace", []))
    plan = payload.get("plan")
    if isinstance(plan, dict):
        if "classification" in plan:
            summary["plan.classification"] = plan.get("classification")
        if "recommended_actions" in plan:
            summary["plan.actions"] = ",".join(str(item) for item in plan.get("recommended_actions", [])[:4])
    return summary or {"keys": sorted(payload.keys())[:8]}


def build_dashboard_graph(events: list[dict[str, Any]], health_data: dict[str, Any], latest: dict[str, Any] | None) -> dict[str, Any]:
    event_types = {event.get("event_type") for event in events}
    active_edges = set()
    for index, edge in enumerate(DASHBOARD_EDGES):
        if any(event_type in event_types for event_type in edge.get("event_types", [])):
            active_edges.add(index)
    temporal_health = health_data.get("temporal", {})
    temporal_status = "online" if temporal_health.get("ok") else "degraded" if temporal_health.get("enabled") else "standby"
    node_status = {
        "external-api": "online",
        "fastapi": "online" if health_data.get("status") == "ok" else "degraded",
        "temporal": temporal_status,
        "langgraph": "active" if latest and latest.get("agent_graph", {}).get("trace") else "standby",
        "red-agent": "active" if "red_plan_created" in event_types else "standby",
        "tool-registry": "online",
        "policy-gateway": "active" if event_types.intersection({"tool_executed", "tool_denied", "tool_duplicate"}) else "standby",
        "tool-executor": "active" if "tool_executed" in event_types else "standby",
        "simulator": "active" if "baseline_ready" in event_types or latest else "standby",
        "truth-state": "online",
        "verifier": "active" if event_types.intersection({"impact_verified", "recovery_verified"}) else "standby",
        "blue-agent": "active" if "classification_completed" in event_types else "standby",
        "recovery": "active" if "safe_containment_entered" in event_types else "standby",
        "judge": "active" if "judge_verdict" in event_types else "standby",
        "evidence-ledger": "online" if health_data.get("database", {}).get("ok") else "degraded",
        "redis": "online" if health_data.get("redis_streams", {}).get("ok") else "standby",
        "openai": "active" if event_types.intersection({"llm_plan_requested", "llm_plan_completed", "llm_plan_failed"}) else ("online" if health_data.get("openai", {}).get("configured") else "standby"),
        "reports": "online",
        "metrics": "online",
        "grafana": "online",
    }
    nodes = [dict(node, status=node_status.get(node["id"], "standby")) for node in DASHBOARD_NODES]
    edges = [dict(edge, id=f"edge-{index}", active=index in active_edges) for index, edge in enumerate(DASHBOARD_EDGES)]
    return {"nodes": nodes, "edges": edges}



def event_activation(event_type: str | None) -> dict[str, list[str]]:
    if not event_type:
        return {"pulse_node_ids": [], "pulse_edge_ids": []}
    edge_ids: list[str] = []
    node_ids: set[str] = set()
    for index, edge in enumerate(DASHBOARD_EDGES):
        if event_type in edge.get("event_types", []):
            edge_ids.append(f"edge-{index}")
            node_ids.add(edge["source"])
            node_ids.add(edge["target"])
    if event_type == "baseline_ready":
        node_ids.update({"simulator", "truth-state"})
    elif event_type in {"langgraph_red_trace", "langgraph_blue_trace", "langgraph_consistency_checked"}:
        node_ids.add("langgraph")
    elif event_type in {"llm_plan_requested", "llm_plan_completed", "llm_plan_failed"}:
        node_ids.add("openai")
    elif event_type in {"temporal_workflow_started", "temporal_workflow_completed", "temporal_workflow_failed"}:
        node_ids.add("temporal")
    elif event_type in {"report_generated", "full_demo_started", "full_demo_completed", "full_demo_failed"}:
        node_ids.update({"fastapi", "reports"})
    return {"pulse_node_ids": sorted(node_ids), "pulse_edge_ids": edge_ids}


def dashboard_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = recent_event_payload([event], limit=1)[0]
    payload.update(event_activation(str(event.get("event_type") or "")))
    return payload


def sse_message(event_name: str, data: dict[str, Any], event_id: int | str | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for line in body.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"

def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    runtime = build_runtime(resolved_settings)
    store = runtime.store
    registry = runtime.registry
    simulator = runtime.simulator
    executor = runtime.executor
    scenario_runner = runtime.scenario_runner
    batch_runner = runtime.batch_runner
    suite_runner = runtime.suite_runner
    replay_runner = runtime.replay_runner
    report_generator = runtime.report_generator
    openai_planner = runtime.openai_planner
    temporal_gateway = TemporalGateway(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        store.init()
        yield

    app = FastAPI(
        title="DAH Agent PoC",
        version="0.1.0",
        description=(
            "Docker-only PoC for a DAH-style Red/Blue agent loop with Mock Simulator, "
            "Tool Registry, Policy Gateway, Verification Layer, and Independent Judge."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = resolved_settings
    app.state.store = store
    app.state.registry = registry
    app.state.event_publisher = runtime.event_publisher

    @app.get("/health")
    def health() -> dict[str, Any]:
        db_ok = False
        try:
            db_ok = store.ping()
        except Exception:
            db_ok = False
        return {
            "status": "ok" if db_ok else "degraded",
            "app": resolved_settings.app_name,
            "env": resolved_settings.app_env,
            "database": {"ok": db_ok, "path": resolved_settings.database_path},
            "openai": {
                "configured": resolved_settings.openai_configured,
                "base_url": resolved_settings.openai_base_url,
                "model": resolved_settings.openai_model,
                "reasoning_effort": resolved_settings.openai_reasoning_effort,
            },
            "tool_registry": registry.names(),
            "temporal": {
                "enabled": resolved_settings.temporal_enabled,
                "address": resolved_settings.temporal_address,
                "namespace": resolved_settings.temporal_namespace,
                "task_queue": resolved_settings.temporal_task_queue,
                "postgres_reuse": {
                    "host": resolved_settings.temporal_db_host,
                    "port": resolved_settings.temporal_db_port,
                    "user_configured": bool(resolved_settings.temporal_db_user.strip()),
                    "password_configured": bool(resolved_settings.temporal_db_password.strip()),
                    "database": resolved_settings.temporal_db_name,
                    "visibility_database": resolved_settings.temporal_visibility_db_name,
                },
            },
            "redis_streams": runtime.event_publisher.ping() if resolved_settings.redis_streams_enabled else {"enabled": False},
            "external_embedding_or_reranker_required": False,
        }

    @app.get("/config")
    def config() -> dict[str, Any]:
        return {
            "app_name": resolved_settings.app_name,
            "app_env": resolved_settings.app_env,
            "database_path": resolved_settings.database_path,
            "public_base_url": resolved_settings.public_base_url,
            "openai_base_url": resolved_settings.openai_base_url,
            "openai_responses_url": resolved_settings.openai_responses_url,
            "openai_model": resolved_settings.openai_model,
            "openai_reasoning_effort": resolved_settings.openai_reasoning_effort,
            "openai_api_key": mask_secret(resolved_settings.openai_api_key),
            "temporal_enabled": resolved_settings.temporal_enabled,
            "temporal_address": resolved_settings.temporal_address,
            "temporal_namespace": resolved_settings.temporal_namespace,
            "temporal_task_queue": resolved_settings.temporal_task_queue,
            "temporal_db_host": resolved_settings.temporal_db_host,
            "temporal_db_port": resolved_settings.temporal_db_port,
            "temporal_db_user_configured": bool(resolved_settings.temporal_db_user.strip()),
            "temporal_db_password_configured": bool(resolved_settings.temporal_db_password.strip()),
            "temporal_db_name": resolved_settings.temporal_db_name,
            "temporal_visibility_db_name": resolved_settings.temporal_visibility_db_name,
            "redis_streams_enabled": resolved_settings.redis_streams_enabled,
            "redis_url": runtime.event_publisher._safe_url(),
            "embedding_reranker": "not_used_for_this_poc",
        }



    async def dashboard_state_payload() -> dict[str, Any]:
        rows = store.list_runs()
        latest_row = rows[-1] if rows else None
        latest = latest_run_summary(latest_row)
        events = store.list_events(latest_row["run_id"]) if latest_row else []
        db_ok = False
        try:
            db_ok = store.ping()
        except Exception:
            db_ok = False
        temporal_status = await temporal_gateway.health()
        health_data = {
            "status": "ok" if db_ok else "degraded",
            "database": {"ok": db_ok, "path": resolved_settings.database_path},
            "openai": {
                "configured": resolved_settings.openai_configured,
                "base_url": resolved_settings.openai_base_url,
                "model": resolved_settings.openai_model,
            },
            "temporal": temporal_status,
            "redis_streams": runtime.event_publisher.ping() if resolved_settings.redis_streams_enabled else {"enabled": False},
        }
        metrics = metric_snapshot(rows)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "refresh_interval_ms": 3000,
            "stream_url": f"{resolved_settings.public_base_url}/dashboard/events",
            "latest_stream_id": store.latest_event_rowid(),
            "health": health_data,
            "graph": build_dashboard_graph(events, health_data, latest),
            "metrics": metrics,
            "latest_run": latest,
            "recent_events": recent_event_payload(events),
            "coverage": build_report_coverage(resolved_settings),
            "links": {
                "api_docs": f"{resolved_settings.public_base_url}/docs",
                "grafana": "http://172.30.1.1:13000",
                "temporal_ui": "http://172.30.1.1:18233",
            },
        }

    @app.get("/dashboard/state")
    async def dashboard_state() -> dict[str, Any]:
        return await dashboard_state_payload()

    @app.get("/dashboard/events")
    async def dashboard_events(request: Request, once: bool = False) -> StreamingResponse:
        async def event_generator():
            header_id = request.headers.get("last-event-id") or request.query_params.get("after")
            try:
                last_stream_id = int(header_id) if header_id else 0
            except ValueError:
                last_stream_id = 0

            state = await dashboard_state_payload()
            latest_stream_id = int(state.get("latest_stream_id") or 0)
            yield sse_message("state", state, latest_stream_id)

            if last_stream_id <= 0:
                replay_events = store.list_recent_events(30)
            else:
                replay_events = store.list_events_after_rowid(last_stream_id, 100)
            for event in replay_events:
                last_stream_id = max(last_stream_id, int(event.get("stream_id") or 0))
                yield sse_message("dah_event", dashboard_event_payload(event), last_stream_id)

            if once:
                return

            last_stream_id = max(last_stream_id, latest_stream_id)
            heartbeat_counter = 0
            while not await request.is_disconnected():
                events = store.list_events_after_rowid(last_stream_id, 100)
                if events:
                    for event in events:
                        last_stream_id = int(event.get("stream_id") or last_stream_id)
                        yield sse_message("dah_event", dashboard_event_payload(event), last_stream_id)
                    state = await dashboard_state_payload()
                    yield sse_message("state", state, last_stream_id)
                else:
                    heartbeat_counter += 1
                    if heartbeat_counter >= 10:
                        heartbeat_counter = 0
                        yield sse_message("heartbeat", {"generated_at": datetime.now(timezone.utc).isoformat()}, last_stream_id)
                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/reports/coverage.json")
    def report_coverage() -> dict[str, Any]:
        return build_report_coverage(resolved_settings)

    @app.get("/streams/status")
    def stream_status() -> dict[str, Any]:
        return runtime.event_publisher.ping()

    def _extract_temporal_result_ids(result: dict[str, Any]) -> tuple[str | None, str | None]:
        payload = result.get("result") if isinstance(result, dict) else {}
        if not isinstance(payload, dict):
            return None, None
        return payload.get("run_id"), payload.get("incident_id")

    async def _execute_temporal_workflow(kind: str, request_payload: dict[str, Any], runner: Any) -> dict[str, Any]:
        store.put_event(
            new_id("evt-temporal-started"),
            "temporal_workflow_started",
            "temporal-gateway",
            {
                "workflow_kind": kind,
                "temporal_enabled": resolved_settings.temporal_enabled,
                "address": resolved_settings.temporal_address,
                "namespace": resolved_settings.temporal_namespace,
                "task_queue": resolved_settings.temporal_task_queue,
            },
        )
        try:
            result = await runner(request_payload)
        except TemporalUnavailable as exc:
            store.put_event(
                new_id("evt-temporal-failed"),
                "temporal_workflow_failed",
                "temporal-gateway",
                {"workflow_kind": kind, "error": str(exc), "status_code": 503},
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            store.put_event(
                new_id("evt-temporal-failed"),
                "temporal_workflow_failed",
                "temporal-gateway",
                {"workflow_kind": kind, "error": str(exc), "status_code": 502},
            )
            raise HTTPException(status_code=502, detail=f"Temporal {kind} workflow failed: {exc}") from exc
        run_id, incident_id = _extract_temporal_result_ids(result)
        store.put_event(
            new_id("evt-temporal-completed"),
            "temporal_workflow_completed",
            "temporal-gateway",
            {
                "workflow_kind": kind,
                "workflow_id": result.get("workflow_id"),
                "namespace": result.get("namespace"),
                "task_queue": result.get("task_queue"),
                "run_id": run_id,
                "incident_id": incident_id,
            },
            run_id=run_id,
            incident_id=incident_id,
        )
        return result

    @app.get("/temporal/health")
    async def temporal_health() -> dict[str, Any]:
        return await temporal_gateway.health()

    @app.post("/temporal/scenarios/run", response_model=TemporalWorkflowResponse)
    async def run_scenario_temporal(request: ScenarioRunRequest) -> dict[str, Any]:
        return await _execute_temporal_workflow("scenario", request.model_dump(), temporal_gateway.execute_scenario)

    @app.post("/temporal/experiments/run-batch", response_model=TemporalWorkflowResponse)
    async def run_batch_temporal(request: BatchExperimentRequest) -> dict[str, Any]:
        return await _execute_temporal_workflow("batch", request.model_dump(), temporal_gateway.execute_batch)

    @app.post("/temporal/experiments/run-suite", response_model=TemporalWorkflowResponse)
    async def run_suite_temporal(request: ExperimentSuiteRequest) -> dict[str, Any]:
        return await _execute_temporal_workflow("suite", request.model_dump(), temporal_gateway.execute_suite)

    @app.post("/scenarios/run", response_model=ScenarioRunResponse)
    def run_scenario(request: ScenarioRunRequest) -> dict[str, Any]:
        return scenario_runner.run(request)

    @app.post("/sim/mission", response_model=MissionSimResponse)
    def simulate_mission(request: MissionSimRequest) -> dict[str, Any]:
        return simulator.simulate_mission(
            request.mission_id,
            request.session_id,
            duration_seconds=request.duration_seconds,
            drop_start_second=request.drop_start_second,
            drop_end_second=request.drop_end_second,
            replay_effect=request.replay_effect,
        )

    @app.get("/scenarios/{run_id}")
    def get_scenario(run_id: str) -> dict[str, Any]:
        row = store.get_run(run_id)
        if not row:
            raise HTTPException(status_code=404, detail="run not found")
        return row

    @app.post("/experiments/run-batch", response_model=BatchExperimentResponse)
    def run_batch_experiment(request: BatchExperimentRequest) -> dict[str, Any]:
        return batch_runner.run_batch(request)

    @app.post("/experiments/run-suite", response_model=ExperimentSuiteResponse)
    def run_experiment_suite(request: ExperimentSuiteRequest) -> dict[str, Any]:
        return suite_runner.run_suite(request)

    @app.post("/replay/{run_id}", response_model=ReplayResponse)
    def replay_run(run_id: str) -> dict[str, Any]:
        try:
            return replay_runner.replay(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.post("/demo/run-full", response_model=FullDemoResponse)
    async def run_full_demo() -> dict[str, Any]:
        demo_id = new_id("demo")
        steps: list[dict[str, Any]] = []
        store.put_event(
            new_id("evt-full-demo-started"),
            "full_demo_started",
            "demo-orchestrator",
            {"demo_id": demo_id, "mode": "local_temporal_suite_llm_report"},
        )
        try:
            local_request = ScenarioRunRequest(
                mission_id="mission-dashboard-full-demo",
                session_id=f"session-full-demo-local-{demo_id}",
                attack_type="selective_message_drop",
                duration_seconds=30,
                use_llm_advisory=True,
            )
            local_result = scenario_runner.run(local_request)
            steps.append(
                {
                    "name": "local_p1_scenario",
                    "status": "completed",
                    "run_id": local_result["run_id"],
                    "incident_id": local_result["incident_id"],
                    "classification": local_result["classification"]["classification"],
                    "llm_openai_used": (local_result.get("llm_plan") or {}).get("openai_used"),
                }
            )

            temporal_status = await temporal_gateway.health()
            try:
                temporal_request = ScenarioRunRequest(
                    mission_id="mission-dashboard-full-demo",
                    session_id=f"session-full-demo-temporal-{demo_id}",
                    attack_type="selective_message_drop",
                    duration_seconds=30,
                    use_llm_advisory=True,
                )
                temporal_result = await _execute_temporal_workflow(
                    "scenario",
                    temporal_request.model_dump(),
                    temporal_gateway.execute_scenario,
                )
                steps.append(
                    {
                        "name": "temporal_p1_scenario",
                        "status": "completed",
                        "workflow_id": temporal_result.get("workflow_id"),
                        "run_id": (temporal_result.get("result") or {}).get("run_id"),
                    }
                )
            except HTTPException as exc:
                steps.append(
                    {
                        "name": "temporal_p1_scenario",
                        "status": "failed",
                        "status_code": exc.status_code,
                        "detail": exc.detail,
                    }
                )

            suite_result = suite_runner.run_suite(
                ExperimentSuiteRequest(
                    mission_id="mission-dashboard-full-demo",
                    session_id_prefix=f"session-full-demo-suite-{demo_id}",
                    runs_per_group=1,
                    duration_seconds=30,
                    use_llm_advisory=False,
                )
            )
            steps.append(
                {
                    "name": "e0_e5_suite",
                    "status": suite_result["status"],
                    "suite_id": suite_result["suite_id"],
                    "total_runs": suite_result["aggregate"]["total_runs"],
                    "average_total_score": suite_result["aggregate"]["average_total_score"],
                }
            )

            direct_llm = await llm_plan(
                LlmPlanRequest(
                    incident={
                        "incident_id": f"inc-full-demo-llm-{demo_id}",
                        "classification": "UNCERTAIN",
                        "planned_defense_actions": ["increase_monitoring_level", "mark_telemetry_untrusted"],
                    },
                    allow_fallback=True,
                )
            )
            steps.append(
                {
                    "name": "direct_llm_advisory",
                    "status": "completed",
                    "openai_used": direct_llm.openai_used,
                    "source": direct_llm.source,
                    "model": direct_llm.model,
                }
            )

            report = report_generator.build(local_result["run_id"])
            report_json_url = f"{resolved_settings.public_base_url}/reports/{local_result['run_id']}.json"
            report_markdown_url = f"{resolved_settings.public_base_url}/reports/{local_result['run_id']}.md"
            report_filename = f"dah-agent-full-demo-{local_result['run_id']}.json"
            steps.append(
                {
                    "name": "report_generation",
                    "status": "completed",
                    "run_id": report["run_id"],
                    "report_json_url": report_json_url,
                    "report_markdown_url": report_markdown_url,
                    "report_filename": report_filename,
                }
            )

            payload = {
                "status": "completed",
                "primary_run_id": local_result["run_id"],
                "primary_incident_id": local_result["incident_id"],
                "report_json_url": report_json_url,
                "report_markdown_url": report_markdown_url,
                "report_filename": report_filename,
                "steps": steps,
                "temporal_enabled": bool(temporal_status.get("enabled")),
                "temporal_ok": temporal_status.get("ok"),
            }
            store.put_event(
                new_id("evt-full-demo-completed"),
                "full_demo_completed",
                "demo-orchestrator",
                {**payload, "demo_id": demo_id},
                run_id=local_result["run_id"],
                incident_id=local_result["incident_id"],
            )
            return payload
        except Exception as exc:
            store.put_event(
                new_id("evt-full-demo-failed"),
                "full_demo_failed",
                "demo-orchestrator",
                {"demo_id": demo_id, "error": str(exc), "steps": steps},
            )
            raise

    @app.get("/reports/{run_id}.json", response_model=ReportResponse)
    def report_json(run_id: str) -> dict[str, Any]:
        try:
            return report_generator.build(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.get("/reports/{run_id}.md")
    def report_markdown(run_id: str) -> Response:
        try:
            report = report_generator.build(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return Response(content=report["markdown"], media_type="text/markdown")



    @app.get("/prometheus/api/v1/labels")
    def prometheus_labels() -> dict[str, Any]:
        return {"status": "success", "data": ["__name__", "job"]}

    @app.get("/prometheus/api/v1/label/__name__/values")
    def prometheus_metric_names() -> dict[str, Any]:
        return {"status": "success", "data": METRIC_NAMES}

    @app.get("/prometheus/api/v1/query")
    def prometheus_query(query: str, time: str | None = None) -> dict[str, Any]:
        metric_name = query.strip().split("{")[0].strip()
        if metric_name in {"1", "1+1"}:
            return prometheus_vector("up", 1.0, parse_time(time))
        if metric_name not in METRIC_NAMES:
            return prometheus_vector(metric_name or "unknown", 0.0, parse_time(time))
        rows = store.list_runs()
        snapshot = metric_snapshot(rows)
        return prometheus_vector(metric_name, snapshot.get(metric_name, 0.0), parse_time(time))

    @app.post("/prometheus/api/v1/query")
    async def prometheus_query_post(request: Request) -> dict[str, Any]:
        params = await prometheus_post_params(request)
        return prometheus_query(params.get("query", ""), params.get("time"))

    @app.get("/prometheus/api/v1/query_range")
    def prometheus_query_range(
        query: str,
        start: str | None = None,
        end: str | None = None,
        step: str = "60",
    ) -> dict[str, Any]:
        metric_name = query.strip().split("{")[0].strip()
        if metric_name not in METRIC_NAMES:
            return prometheus_matrix(metric_name or "unknown", [])
        start_ts = parse_time(start)
        end_ts = parse_time(end)
        rows = [row for row in store.list_runs() if start_ts <= run_timestamp(row) <= end_ts]
        values: list[list[Any]] = []
        for index in range(len(rows)):
            subset = rows[: index + 1]
            ts = run_timestamp(rows[index])
            values.append([ts, str(metric_snapshot(subset).get(metric_name, 0.0))])
        if not values:
            values.append([end_ts, "0"])
        return prometheus_matrix(metric_name, values)

    @app.post("/prometheus/api/v1/query_range")
    async def prometheus_query_range_post(request: Request) -> dict[str, Any]:
        params = await prometheus_post_params(request)
        return prometheus_query_range(
            params.get("query", ""),
            params.get("start"),
            params.get("end"),
            params.get("step", "60"),
        )

    @app.get("/tools/registry")
    def tool_registry() -> dict[str, Any]:
        return {
            "source": registry.source,
            "version": registry.version,
            "load_error": registry.load_error,
            "names": registry.names(),
            "contracts": registry.contracts(),
        }

    @app.post("/tools/execute", response_model=ToolExecutionResponse)
    def execute_tool(request: ToolExecutionRequest) -> ToolExecutionResponse:
        return executor.execute(request)

    @app.get("/truth/sessions/{session_id}")
    def get_truth_session(session_id: str) -> dict[str, Any]:
        truth = store.get_truth(session_id)
        if not truth:
            raise HTTPException(status_code=404, detail="truth session not found")
        return truth.model_dump()

    @app.get("/truth/mission/{mission_id}")
    def get_truth_mission(mission_id: str) -> dict[str, Any]:
        sessions = store.list_truth_by_mission(mission_id)
        return {"mission_id": mission_id, "sessions": [item.model_dump() for item in sessions]}

    @app.get("/truth/events")
    def truth_events(
        run_id: str | None = None,
        incident_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 500))
        events = store.list_events(run_id) if run_id else store.list_recent_events(bounded_limit)
        if incident_id:
            events = [event for event in events if event.get("incident_id") == incident_id]
        return {
            "run_id": run_id,
            "incident_id": incident_id,
            "count": len(events[:bounded_limit]),
            "events": events[:bounded_limit],
        }

    def _enrich_llm_response(response: LlmPlanResponse) -> LlmPlanResponse:
        payload = response.model_dump()
        payload["model"] = resolved_settings.openai_model
        payload["base_url"] = resolved_settings.openai_base_url
        payload["applied_to_execution"] = False
        recommended = [str(item) for item in payload.get("plan", {}).get("recommended_actions", [])]
        allowed = [item for item in recommended if registry.canonical_name(item)]
        payload["recommended_actions_allowed"] = allowed
        payload["recommended_actions_denied"] = [item for item in recommended if item not in allowed]
        return LlmPlanResponse.model_validate(payload)

    @app.post("/llm/plan", response_model=LlmPlanResponse)
    async def llm_plan(request: LlmPlanRequest) -> LlmPlanResponse:
        incident_id = request.incident.get("incident_id") if isinstance(request.incident, dict) else None
        incident_id = str(incident_id) if incident_id else None
        store.put_event(
            new_id("evt-llm-plan-requested"),
            "llm_plan_requested",
            "llm-advisory-planner",
            {
                "trigger": "direct_api",
                "model": resolved_settings.openai_model,
                "base_url": resolved_settings.openai_base_url,
                "openai_configured": resolved_settings.openai_configured,
                "allow_fallback": request.allow_fallback,
                "applied_to_execution": False,
            },
            incident_id=incident_id,
        )
        try:
            response = _enrich_llm_response(await openai_planner.plan(request.incident, request.allow_fallback))
        except RuntimeError as exc:
            store.put_event(
                new_id("evt-llm-plan-failed"),
                "llm_plan_failed",
                "llm-advisory-planner",
                {"trigger": "direct_api", "error": str(exc), "allow_fallback": request.allow_fallback},
                incident_id=incident_id,
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            if not request.allow_fallback:
                store.put_event(
                    new_id("evt-llm-plan-failed"),
                    "llm_plan_failed",
                    "llm-advisory-planner",
                    {"trigger": "direct_api", "error": str(exc), "allow_fallback": request.allow_fallback},
                    incident_id=incident_id,
                )
                raise HTTPException(status_code=502, detail=f"OpenAI plan failed: {exc}") from exc
            response = _enrich_llm_response(
                LlmPlanResponse(
                    openai_used=False,
                    source="rule_fallback_after_openai_error",
                    plan={
                        "classification": request.incident.get("classification", "UNCERTAIN"),
                        "recommended_actions": ["increase_monitoring_level", "mark_telemetry_untrusted"],
                        "rationale": f"OpenAI call failed; conservative fallback used: {exc}",
                        "safety_notes": ["No state change is executed by the LLM path."],
                    },
                    error=str(exc),
                )
            )
        store.put_event(
            new_id("evt-llm-plan-completed"),
            "llm_plan_completed",
            "llm-advisory-planner",
            {**response.model_dump(), "trigger": "direct_api"},
            incident_id=incident_id,
        )
        return response

    return app


app = create_app()

