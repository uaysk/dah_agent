from __future__ import annotations

from typing import Any

from .config import Settings


def build_report_coverage(settings: Settings) -> dict[str, Any]:
    items = [
        ("P1 coordinate-report suppression", "implemented", ["/scenarios/run attack_type=selective_message_drop", "simulate_selective_message_drop"]),
        ("P2/E5 recovery interference", "implemented", ["/scenarios/run attack_type=recovery_interference", "safe_containment_entered event"]),
        ("P3 display replay-effect", "implemented", ["/scenarios/run attack_type=display_replay_effect", "simulate_display_replay_effect"]),
        ("UAV/UGV mission simulation", "implemented", ["/sim/mission", "mission_simulation.trace", "component_logs/message_id/timestamp_utc"]),
        ("Red/Blue closed loop", "implemented", ["ScenarioRunner", "RedAgent", "BlueAgent"]),
        ("Tool Registry + Policy Gateway", "implemented", ["/tools/registry", "/tools/execute", "app/policy/tool_registry.yaml", "policy_gateway decision payload"]),
        ("Safety invariant boundary", "implemented", ["unknown/A4 tool denial", "prompt_input_sanitized event"]),
        ("Independent Judge", "implemented", ["judge_verdict", "judge_audit_event"]),
        ("Evidence Ledger common fields", "implemented", ["evidence_ledger.records", "evidence_records table"]),
        ("Replay Harness", "implemented", ["/replay/{run_id}", "replay_runs table"]),
        ("E0-E5 experiment suite", "implemented", ["/experiments/run-suite", "false_positive_rate/false_negative_rate metrics"]),
        ("LangGraph agent reasoning trace", "implemented", ["agent_graph.framework=langgraph", "langgraph_red_trace/langgraph_blue_trace events", "langgraph_consistency_checked"]),
        ("Temporal durable workflow", "implemented", ["/temporal/scenarios/run", "temporal-worker", "Temporal server profile"]),
        ("Existing K8s PostgreSQL reuse for Temporal", "implemented", ["docker-compose temporal profile", "TEMPORAL_DB_HOST=172.30.1.49 default"]),
        ("Redis Streams event bus", "implemented_optional", ["REDIS_STREAMS_ENABLED", "dah:* stream mapping", "agent/llm/workflow/report streams", "dlq-events"]),
        ("Grafana visualization", "implemented", ["grafana dashboard", "/prometheus compatible endpoint"]),
        ("LiteLLM/OpenAI advisory path", "implemented", ["OPENAI_BASE_URL=https://litellm.uaysk.com", "/scenarios/run llm_plan", "/llm/plan", "llm_plan_requested/completed/failed events"]),
        ("Embedding/reranker dependency", "not_required", ["external_embedding_or_reranker_required=false"]),
    ]
    return {
        "status": "implemented_for_poc",
        "homelab_fit": {
            "new_containers": ["dah-agent-poc", "dashboard", "grafana", "temporal(optional)", "temporal-ui(optional)", "temporal-worker(optional)"],
            "reused_k8s_services": {
                "postgresql": "172.30.1.49:5432 via TEMPORAL_DB_HOST",
                "redis_streams_optional": settings.redis_url,
            },
            "resource_profile": {
                "temporal_server_memory_target": "512-768 MiB",
                "temporal_ui_memory_target": "128-256 MiB",
                "temporal_worker_memory_target": "128-256 MiB",
                "current_vm_available_memory_reference": "about 7.4 GiB available at last check",
            },
        },
        "items": [
            {"requirement": requirement, "status": status, "evidence": evidence}
            for requirement, status, evidence in items
        ],
    }
