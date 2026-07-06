from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class AgentGraphState(TypedDict, total=False):
    mission_id: str
    session_id: str
    incident_id: str
    attack_type: str
    target_flow_id: str
    command_type: str
    duration_seconds: int
    seed: int
    observation_present: bool
    impact: dict[str, Any]
    classification: dict[str, Any]
    defense_plan: dict[str, Any]
    trace: list[dict[str, Any]]
    decisions: dict[str, Any]


def _append(state: AgentGraphState, node: str, detail: dict[str, Any]) -> list[dict[str, Any]]:
    trace = list(state.get("trace", []))
    trace.append({"node": node, "detail": detail})
    return trace


def _red_observe(state: AgentGraphState) -> dict[str, Any]:
    detail = {
        "mission_id": state["mission_id"],
        "session_id": state["session_id"],
        "target_flow_id": state["target_flow_id"],
        "observation_present": state.get("observation_present", False),
    }
    return {"trace": _append(state, "red.observe", detail)}


def _red_generate_hypothesis(state: AgentGraphState) -> dict[str, Any]:
    attack_type = state["attack_type"]
    mapping = {
        "selective_message_drop": ("H1_coordinate_report_suppression", "P1"),
        "selective_command_drop": ("H1_coordinate_report_suppression", "P1"),
        "display_replay_effect": ("H2_display_replay_effect", "P3"),
        "recovery_interference": ("H3_recovery_interference", "P2_E5"),
        "random_packet_loss": ("H0_fault_profile", "E1"),
        "normal_baseline": ("H0_baseline", "E0"),
    }
    hypothesis, scenario_path = mapping.get(attack_type, ("H_unknown", "UNKNOWN"))
    decisions = dict(state.get("decisions", {}))
    decisions.update({"red_hypothesis": hypothesis, "scenario_path": scenario_path})
    return {
        "decisions": decisions,
        "trace": _append(
            state,
            "red.generate_hypothesis",
            {"attack_type": attack_type, "hypothesis": hypothesis, "scenario_path": scenario_path},
        ),
    }


def _red_plan_attack(state: AgentGraphState) -> dict[str, Any]:
    tool_by_attack = {
        "selective_message_drop": "simulate_selective_message_drop",
        "selective_command_drop": "simulate_selective_message_drop",
        "display_replay_effect": "simulate_display_replay_effect",
        "recovery_interference": "simulate_recovery_interference",
        "random_packet_loss": "simulate_random_packet_loss",
        "normal_baseline": "simulate_normal_baseline",
    }
    tool_name = tool_by_attack.get(state["attack_type"], "simulate_selective_message_drop")
    decisions = dict(state.get("decisions", {}))
    decisions.update({"red_selected_tool": tool_name, "red_policy_stage": "policy_prepare_only"})
    return {
        "decisions": decisions,
        "trace": _append(
            state,
            "red.plan_attack",
            {"selected_tool": tool_name, "policy_boundary": "Single Tool Executor"},
        ),
    }


def _blue_observe_evidence(state: AgentGraphState) -> dict[str, Any]:
    impact = state.get("impact", {})
    result = impact.get("result", {}) if isinstance(impact, dict) else {}
    detail = {
        "command_gap": result.get("command_gap"),
        "max_consecutive_gap_seconds": result.get("max_consecutive_gap_seconds"),
        "fault_profile": result.get("fault_profile"),
        "technical_success": result.get("technical_success"),
    }
    return {"trace": _append(state, "blue.observe_evidence", detail)}


def _blue_classify(state: AgentGraphState) -> dict[str, Any]:
    classification = state.get("classification", {})
    decisions = dict(state.get("decisions", {}))
    decisions.update(
        {
            "blue_classification": classification.get("classification"),
            "blue_attack_score": classification.get("attack_score"),
            "blue_fault_score": classification.get("fault_score"),
        }
    )
    return {
        "decisions": decisions,
        "trace": _append(
            state,
            "blue.classify",
            {
                "classification": classification.get("classification"),
                "attack_score": classification.get("attack_score"),
                "fault_score": classification.get("fault_score"),
            },
        ),
    }


def _blue_plan_defense(state: AgentGraphState) -> dict[str, Any]:
    plan = state.get("defense_plan", {})
    actions = plan.get("actions", []) if isinstance(plan, dict) else []
    tool_names = [item.get("tool_name") for item in actions if isinstance(item, dict)]
    decisions = dict(state.get("decisions", {}))
    decisions.update({"blue_defense_actions": tool_names})
    return {
        "decisions": decisions,
        "trace": _append(
            state,
            "blue.plan_defense",
            {"action_count": len(tool_names), "actions": tool_names},
        ),
    }


def _blue_recovery_gate(state: AgentGraphState) -> dict[str, Any]:
    actions = state.get("decisions", {}).get("blue_defense_actions", [])
    safe_containment_possible = "request_state_resynchronization" in actions
    decisions = dict(state.get("decisions", {}))
    decisions.update({"blue_recovery_gate": "resync_then_validate", "safe_containment_possible": safe_containment_possible})
    return {
        "decisions": decisions,
        "trace": _append(
            state,
            "blue.recovery_gate",
            {
                "recovery_gate": "resync_then_validate",
                "safe_containment_possible": safe_containment_possible,
            },
        ),
    }


def _build_red_graph() -> Any:
    graph = StateGraph(AgentGraphState)
    graph.add_node("red.observe", _red_observe)
    graph.add_node("red.generate_hypothesis", _red_generate_hypothesis)
    graph.add_node("red.plan_attack", _red_plan_attack)
    graph.add_edge(START, "red.observe")
    graph.add_edge("red.observe", "red.generate_hypothesis")
    graph.add_edge("red.generate_hypothesis", "red.plan_attack")
    graph.add_edge("red.plan_attack", END)
    return graph.compile()


def _build_blue_graph() -> Any:
    graph = StateGraph(AgentGraphState)
    graph.add_node("blue.observe_evidence", _blue_observe_evidence)
    graph.add_node("blue.classify", _blue_classify)
    graph.add_node("blue.plan_defense", _blue_plan_defense)
    graph.add_node("blue.recovery_gate", _blue_recovery_gate)
    graph.add_edge(START, "blue.observe_evidence")
    graph.add_edge("blue.observe_evidence", "blue.classify")
    graph.add_edge("blue.classify", "blue.plan_defense")
    graph.add_edge("blue.plan_defense", "blue.recovery_gate")
    graph.add_edge("blue.recovery_gate", END)
    return graph.compile()


class LangGraphAgentAdapter:
    def __init__(self) -> None:
        self.red_graph = _build_red_graph()
        self.blue_graph = _build_blue_graph()

    def run_red_graph(self, request: Any, incident_id: str) -> dict[str, Any]:
        initial: AgentGraphState = {
            "mission_id": request.mission_id,
            "session_id": request.session_id,
            "incident_id": incident_id,
            "attack_type": request.attack_type,
            "target_flow_id": request.target_flow_id,
            "command_type": request.command_type,
            "duration_seconds": request.duration_seconds,
            "seed": request.seed,
            "observation_present": bool(getattr(request, "observation_text", "")),
            "trace": [],
            "decisions": {},
        }
        output = self.red_graph.invoke(initial)
        return {
            "graph": "red_agent",
            "framework": "langgraph",
            "nodes": ["red.observe", "red.generate_hypothesis", "red.plan_attack"],
            "trace": output.get("trace", []),
            "decisions": output.get("decisions", {}),
        }

    def run_blue_graph(self, impact: Any, classification: Any, defense_plan: Any) -> dict[str, Any]:
        initial: AgentGraphState = {
            "impact": impact.model_dump(),
            "classification": classification.model_dump(),
            "defense_plan": defense_plan.model_dump(by_alias=True),
            "trace": [],
            "decisions": {},
        }
        output = self.blue_graph.invoke(initial)
        return {
            "graph": "blue_agent",
            "framework": "langgraph",
            "nodes": [
                "blue.observe_evidence",
                "blue.classify",
                "blue.plan_defense",
                "blue.recovery_gate",
            ],
            "trace": output.get("trace", []),
            "decisions": output.get("decisions", {}),
        }

    def combine(self, red_graph: dict[str, Any], blue_graph: dict[str, Any], llm_plan: dict[str, Any] | None = None) -> dict[str, Any]:
        trace = [*red_graph.get("trace", []), *blue_graph.get("trace", [])]
        decisions = {**red_graph.get("decisions", {}), **blue_graph.get("decisions", {})}
        graphs = [red_graph, blue_graph]
        if llm_plan:
            plan = llm_plan.get("plan") or {}
            llm_trace = {
                "graph": "llm_advisory",
                "framework": "openai_responses",
                "nodes": ["llm.advisory_plan"],
                "trace": [
                    {
                        "node": "llm.advisory_plan",
                        "detail": {
                            "openai_used": llm_plan.get("openai_used"),
                            "source": llm_plan.get("source"),
                            "model": llm_plan.get("model"),
                            "recommended_actions": plan.get("recommended_actions", []),
                            "applied_to_execution": llm_plan.get("applied_to_execution", False),
                        },
                    }
                ],
                "decisions": {
                    "llm_openai_used": llm_plan.get("openai_used"),
                    "llm_source": llm_plan.get("source"),
                    "llm_model": llm_plan.get("model"),
                    "llm_applied_to_execution": llm_plan.get("applied_to_execution", False),
                },
            }
            graphs.append(llm_trace)
            trace.extend(llm_trace["trace"])
            decisions.update(llm_trace["decisions"])
        return {
            "framework": "langgraph",
            "durable_state_owner": "temporal_or_local_runner",
            "purpose": "agent_reasoning_trace_adapter",
            "graphs": graphs,
            "trace": trace,
            "decisions": decisions,
        }
