from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


RiskLevel = Literal["A0", "A1", "A2", "A3", "A4"]
PolicyResult = Literal["ALLOW", "ACTION_DENIED", "DUPLICATE_IGNORED"]
Classification = Literal[
    "WATCH",
    "FAULT_SUSPECTED",
    "ATTACK_SUSPECTED",
    "ATTACK_CONFIRMED",
    "UNCERTAIN",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class ScenarioRunRequest(BaseModel):
    mission_id: str = "mission-alpha"
    session_id: str = "session-17"
    attack_type: Literal[
        "selective_message_drop",
        "selective_command_drop",
        "display_replay_effect",
        "recovery_interference",
        "random_packet_loss",
        "normal_baseline",
    ] = "selective_message_drop"
    target_flow_id: str = "flow-return-042"
    command_type: str = "RESYNC"
    duration_seconds: int = Field(default=30, ge=1, le=300)
    seed: int = 26063001
    use_llm_advisory: bool = True
    use_llm_for_uncertain: bool | None = Field(
        default=None,
        description="Deprecated compatibility flag. Set false to disable the advisory LLM path.",
    )
    observation_text: str = ""


class MissionSimRequest(BaseModel):
    mission_id: str = "mission-alpha"
    session_id: str = "session-sim"
    duration_seconds: int = Field(default=60, ge=1, le=600)
    drop_start_second: int | None = Field(default=None, ge=0)
    drop_end_second: int | None = Field(default=None, ge=0)
    replay_effect: bool = False


class MissionSimResponse(BaseModel):
    summary: dict[str, Any]
    trace: list[dict[str, Any]]


class ToolExecutionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(default_factory=lambda: new_id("evt-tool"))
    incident_id: str
    tool_name: str
    risk_level: RiskLevel = "A2"
    idempotency_key: str
    target: dict[str, Any] = Field(default_factory=dict)
    input_data: dict[str, Any] = Field(default_factory=dict, alias="input")
    requested_by: str = "agent-workflow"


class ToolExecutionResponse(BaseModel):
    event_id: str
    incident_id: str
    tool_name: str
    policy_result: PolicyResult
    accepted: bool
    status: str
    audit_event_id: str
    result: dict[str, Any] = Field(default_factory=dict)


class TruthSession(BaseModel):
    session_id: str
    mission_id: str
    status: str = "active"
    quarantined: bool = False
    monitoring_level: str = "normal"
    command_sent_count: int = 10
    command_delivered_count: int = 10
    last_command_id: str = "cmd-010"
    last_vehicle_received_command_id: str = "cmd-010"
    telemetry_fresh: bool = True
    telemetry_untrusted: bool = False
    ugv_state: str = "MISSION_ACTIVE"
    active_injections: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)


class VerificationResult(BaseModel):
    verification_id: str = Field(default_factory=lambda: new_id("ver"))
    incident_id: str
    success: bool
    result: dict[str, Any]


class ClassificationResult(BaseModel):
    classification: Classification
    attack_score: float
    fault_score: float
    evidence: list[str]


class DefensePlan(BaseModel):
    classification: Classification
    actions: list[ToolExecutionRequest]
    rationale: str


class JudgeVerdict(BaseModel):
    verdict_id: str = Field(default_factory=lambda: new_id("judge"))
    incident_id: str
    mission_id: str
    attack_score: int
    defense_score: int
    availability: int
    total_score: float
    labels: list[str]
    reason: dict[str, Any]


class ScenarioRunResponse(BaseModel):
    run_id: str
    incident_id: str
    status: str
    red_plan: dict[str, Any]
    attack_result: ToolExecutionResponse
    impact_verification: VerificationResult
    classification: ClassificationResult
    defense_plan: DefensePlan
    defense_results: list[ToolExecutionResponse]
    restore_result: ToolExecutionResponse
    recovery_verification: VerificationResult
    judge_verdict: JudgeVerdict
    truth_state: TruthSession
    mission_simulation: dict[str, Any] = Field(default_factory=dict)
    safety_transitions: list[dict[str, Any]] = Field(default_factory=list)
    llm_plan: LlmPlanResponse | None = None
    agent_graph: dict[str, Any] = Field(default_factory=dict)
    evidence_ledger_records: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]]


class LlmPlanRequest(BaseModel):
    incident: dict[str, Any]
    allow_fallback: bool = True


class LlmPlanResponse(BaseModel):
    openai_used: bool
    source: str
    plan: dict[str, Any]
    model: str | None = None
    base_url: str | None = None
    applied_to_execution: bool = False
    recommended_actions_allowed: list[str] = Field(default_factory=list)
    recommended_actions_denied: list[str] = Field(default_factory=list)
    error: str | None = None


class BatchExperimentRequest(BaseModel):
    mission_id: str = "mission-alpha"
    session_id_prefix: str = "session-batch"
    attack_type: Literal[
        "selective_message_drop",
        "selective_command_drop",
        "display_replay_effect",
        "recovery_interference",
        "random_packet_loss",
        "normal_baseline",
    ] = "selective_message_drop"
    target_flow_id: str = "flow-return-042"
    command_type: str = "RESYNC"
    duration_seconds: int = Field(default=30, ge=1, le=300)
    runs: int = Field(default=30, ge=1, le=100)
    seed_start: int = 26063001
    use_llm_advisory: bool = True


class BatchExperimentResponse(BaseModel):
    experiment_id: str
    status: str
    run_count: int
    run_ids: list[str]
    metrics: dict[str, Any]
    csv: str


class ExperimentSuiteRequest(BaseModel):
    mission_id: str = "mission-alpha"
    session_id_prefix: str = "session-suite"
    runs_per_group: int = Field(default=3, ge=1, le=30)
    duration_seconds: int = Field(default=30, ge=1, le=300)
    seed_start: int = 26063001
    use_llm_advisory: bool = True


class ExperimentSuiteResponse(BaseModel):
    suite_id: str
    status: str
    groups: list[dict[str, Any]]
    aggregate: dict[str, Any]


class ReplayResponse(BaseModel):
    source_run_id: str
    replay_run_id: str
    policy_match: bool
    judge_match: bool
    deterministic: bool
    comparison: dict[str, Any]


class ReportResponse(BaseModel):
    run_id: str
    markdown: str
    json_report: dict[str, Any]


class FullDemoResponse(BaseModel):
    status: str
    primary_run_id: str
    primary_incident_id: str
    report_json_url: str
    report_markdown_url: str
    report_filename: str
    steps: list[dict[str, Any]]
    temporal_enabled: bool
    temporal_ok: bool | None = None


class TemporalWorkflowResponse(BaseModel):
    workflow_id: str
    namespace: str
    task_queue: str
    result: dict[str, Any]
