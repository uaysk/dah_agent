from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .judge.evidence_ledger import build_evidence_ledger
from .langgraph_adapter import LangGraphAgentAdapter
from .policy.policy_gateway import POLICY_VERSION, evaluate_action
from .models import (
    BatchExperimentRequest,
    ExperimentSuiteRequest,
    ClassificationResult,
    DefensePlan,
    JudgeVerdict,
    LlmPlanResponse,
    ScenarioRunRequest,
    ToolExecutionRequest,
    ToolExecutionResponse,
    TruthSession,
    VerificationResult,
    new_id,
    now_iso,
)
from .store import Store


RISK_ORDER = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}


@dataclass(frozen=True)
class ToolContract:
    name: str
    risk_level: str
    aliases: tuple[str, ...] = ()
    purpose: str = ""
    timeout_seconds: int = 30
    retry_attempts: int = 1
    compensation: str | None = None


class ToolRegistry:
    def __init__(self, registry_path: str | Path | None = None) -> None:
        self.registry_path = Path(registry_path) if registry_path else Path(__file__).resolve().parent / "policy" / "tool_registry.yaml"
        self.source = "default-contracts"
        self.version = "tool-registry-default-v1"
        self.load_error: str | None = None
        contracts = self._default_contracts()
        if self.registry_path.exists():
            try:
                contracts = self._load_yaml_contracts(self.registry_path, contracts)
                self.source = str(self.registry_path)
            except Exception as exc:
                self.load_error = str(exc)
        self._by_name: dict[str, ToolContract] = {}
        self._canonical: dict[str, str] = {}
        for contract in contracts:
            self._by_name[contract.name] = contract
            self._canonical[contract.name] = contract.name
            for alias in contract.aliases:
                self._canonical[alias] = contract.name

    def _default_contracts(self) -> list[ToolContract]:
        return [
            ToolContract("simulate_selective_message_drop", "A2", ("simulate_selective_command_drop",), "mock Return Link coordinate-report suppression", 60, 1, "restore_attack_injection_state"),
            ToolContract("simulate_display_replay_effect", "A2", (), "mock display replay-effect injection", 60, 1, "restore_attack_injection_state"),
            ToolContract("simulate_recovery_interference", "A2", ("recovery_interference",), "bounded E5 recovery/resync interference", 60, 1, "restore_attack_injection_state"),
            ToolContract("simulate_random_packet_loss", "A1", ("random_packet_loss",), "non-adversarial E1 packet-loss fault profile", 60, 1, None),
            ToolContract("simulate_normal_baseline", "A0", ("normal_baseline",), "E0 no-attack baseline mission trace", 60, 1, None),
            ToolContract("restore_attack_injection_state", "A2", (), "compensate and remove mock attack injection", 30, 2, None),
            ToolContract("increase_monitoring_level", "A1", (), "raise monitoring on the affected mock session", 30, 2, None),
            ToolContract("mark_telemetry_untrusted", "A1", (), "mark affected telemetry stream untrusted until resync", 30, 2, None),
            ToolContract("quarantine_link_session", "A2", (), "isolate the affected mock link session", 30, 2, None),
            ToolContract("request_state_resynchronization", "A2", (), "request validated state resynchronization", 30, 2, None),
            ToolContract("restore_validated_session", "A2", (), "restore normal validated session state after cleanup", 30, 2, None),
        ]

    def _load_yaml_contracts(self, path: Path, defaults: list[ToolContract]) -> list[ToolContract]:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load the YAML tool registry") from exc
        data = yaml.safe_load(path.read_text()) or {}
        self.version = str(data.get("version") or self.version)
        tools = data.get("tools") or {}
        if not isinstance(tools, dict):
            raise ValueError("tool_registry.yaml tools must be a mapping")
        by_name = {item.name: item for item in defaults}
        for name, spec in tools.items():
            if not isinstance(spec, dict):
                raise ValueError(f"tool registry entry for {name} must be a mapping")
            current = by_name.get(name, ToolContract(str(name), "A2"))
            aliases = spec.get("aliases", current.aliases)
            if aliases is None:
                aliases = ()
            by_name[str(name)] = ToolContract(
                name=str(name),
                risk_level=str(spec.get("risk_level") or current.risk_level),
                aliases=tuple(str(item) for item in aliases),
                purpose=str(spec.get("purpose") or current.purpose),
                timeout_seconds=int(spec.get("timeout_seconds") or current.timeout_seconds),
                retry_attempts=int(spec.get("retry_attempts") or current.retry_attempts),
                compensation=(str(spec["compensation"]) if spec.get("compensation") else current.compensation),
            )
        return list(by_name.values())

    def canonical_name(self, tool_name: str) -> str | None:
        return self._canonical.get(tool_name)

    def get(self, tool_name: str) -> ToolContract | None:
        canonical = self.canonical_name(tool_name)
        if not canonical:
            return None
        return self._by_name[canonical]

    def names(self) -> list[str]:
        return sorted(self._canonical.keys())

    def contracts(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "risk_level": item.risk_level,
                "aliases": list(item.aliases),
                "purpose": item.purpose,
                "timeout_seconds": item.timeout_seconds,
                "retry_attempts": item.retry_attempts,
                "compensation": item.compensation,
            }
            for item in sorted(self._by_name.values(), key=lambda contract: contract.name)
        ]

    def metadata(self, contract: ToolContract | None = None) -> dict[str, Any]:
        payload = {
            "source": self.source,
            "version": self.version,
            "load_error": self.load_error,
        }
        if contract:
            payload.update(
                {
                    "canonical_tool_name": contract.name,
                    "contract_risk_level": contract.risk_level,
                    "purpose": contract.purpose,
                    "timeout_seconds": contract.timeout_seconds,
                    "retry_attempts": contract.retry_attempts,
                    "compensation": contract.compensation,
                }
            )
        return payload


class Simulator:
    def __init__(self, store: Store) -> None:
        self.store = store

    def simulate_mission(
        self,
        mission_id: str,
        session_id: str,
        duration_seconds: int = 60,
        drop_start_second: int | None = None,
        drop_end_second: int | None = None,
        replay_effect: bool = False,
    ) -> dict[str, Any]:
        uav = {"x": 0.0, "y": 0.0}
        ugv = {"x": -20.0, "y": -10.0}
        waypoint = {"x": 60.0, "y": 30.0}
        uav_speed = 2.0
        ugv_speed = 1.5
        last_valid_coord: dict[str, Any] | None = None
        last_delivered_second: int | None = None
        safe_stop_second: int | None = None
        delivered_reports = 0
        dropped_reports = 0
        display_mismatches = 0
        trace: list[dict[str, Any]] = []
        started_at = datetime.now(timezone.utc)

        for second in range(duration_seconds + 1):
            self._move_toward(uav, waypoint, uav_speed)
            timestamp_utc = (started_at + timedelta(seconds=second)).isoformat()
            message_id = f"{mission_id}:{session_id}:coord:{second + 1:06d}"
            coord_report = {
                "mission_id": mission_id,
                "session_id": session_id,
                "message_id": message_id,
                "sequence_number": second + 1,
                "timestamp_utc": timestamp_utc,
                "coord_created_at_second": second,
                "uav_position": {"x": round(uav["x"], 2), "y": round(uav["y"], 2)},
            }
            dropped = drop_start_second is not None and drop_start_second <= second <= (drop_end_second or drop_start_second)
            if dropped:
                dropped_reports += 1
            else:
                delivered_reports += 1
                last_valid_coord = coord_report
                last_delivered_second = second

            coord_age = None if last_valid_coord is None else second - int(last_valid_coord["coord_created_at_second"])
            coord_fresh = coord_age is not None and coord_age <= 5
            if safe_stop_second is not None:
                ugv_state = "SAFE_STOP_CAUSED_BY_COORD_STALE"
            elif coord_age is not None and coord_age >= 15:
                ugv_state = "SAFE_STOP_CAUSED_BY_COORD_STALE"
                safe_stop_second = second
            elif coord_fresh and last_valid_coord is not None:
                self._move_toward(ugv, last_valid_coord["uav_position"], ugv_speed)
                ugv_state = "MOVING_TO_COORD"
            else:
                ugv_state = "WAITING_FOR_FRESH_COORD"

            displayed_coord_age = coord_age
            if replay_effect and last_valid_coord is not None and second >= max(1, duration_seconds // 3):
                displayed_coord_age = max(0, coord_age or 0) + 10
            display_truth_mismatch = replay_effect and displayed_coord_age != coord_age
            if display_truth_mismatch:
                display_mismatches += 1

            component_logs = [
                {
                    "component": "UAV",
                    "event": "coord_report_created",
                    "message_id": message_id,
                    "sequence_number": second + 1,
                    "timestamp_utc": timestamp_utc,
                    "position": coord_report["uav_position"],
                },
                {
                    "component": "SATELLITE_GATEWAY",
                    "event": "return_link_forwarding_decision",
                    "message_id": message_id,
                    "sequence_number": second + 1,
                    "timestamp_utc": timestamp_utc,
                    "dropped": dropped,
                },
                {
                    "component": "C2_COORDINATION",
                    "event": "coord_freshness_evaluated",
                    "message_id": message_id,
                    "sequence_number": second + 1,
                    "timestamp_utc": timestamp_utc,
                    "coord_age_seconds": coord_age,
                    "coord_fresh": coord_fresh,
                },
                {
                    "component": "GCS_DISPLAY",
                    "event": "display_truth_compared",
                    "message_id": message_id,
                    "sequence_number": second + 1,
                    "timestamp_utc": timestamp_utc,
                    "display_truth_mismatch": display_truth_mismatch,
                },
                {
                    "component": "UGV",
                    "event": "motion_state_updated",
                    "message_id": message_id,
                    "sequence_number": second + 1,
                    "timestamp_utc": timestamp_utc,
                    "state": ugv_state,
                    "position": {"x": round(ugv["x"], 2), "y": round(ugv["y"], 2)},
                },
            ]
            trace.append(
                {
                    "t": second,
                    "timestamp_utc": timestamp_utc,
                    "message_id": message_id,
                    "sequence_number": second + 1,
                    "component_logs": component_logs,
                    "uav": {"x": round(uav["x"], 2), "y": round(uav["y"], 2)},
                    "ugv": {"x": round(ugv["x"], 2), "y": round(ugv["y"], 2), "state": ugv_state},
                    "gateway": {"return_link_report_dropped": dropped, "delivered_reports": delivered_reports},
                    "telemetry": {
                        "coord_age_seconds": coord_age,
                        "coord_fresh": coord_fresh,
                        "last_delivered_second": last_delivered_second,
                    },
                    "gcs_display": {
                        "displayed_coord_age_seconds": displayed_coord_age,
                        "display_truth_mismatch": display_truth_mismatch,
                    },
                }
            )

        max_stale_gap = 0
        current_gap = 0
        for item in trace:
            if item["gateway"]["return_link_report_dropped"]:
                current_gap += 1
                max_stale_gap = max(max_stale_gap, current_gap)
            else:
                current_gap = 0
        summary = {
            "mission_id": mission_id,
            "session_id": session_id,
            "duration_seconds": duration_seconds,
            "uav_final_position": trace[-1]["uav"],
            "ugv_final_position": {"x": trace[-1]["ugv"]["x"], "y": trace[-1]["ugv"]["y"]},
            "ugv_final_state": trace[-1]["ugv"]["state"],
            "safe_stop_triggered": safe_stop_second is not None,
            "safe_stop_second": safe_stop_second,
            "delivered_reports": delivered_reports,
            "dropped_reports": dropped_reports,
            "max_consecutive_coordinate_gap_seconds": max_stale_gap,
            "display_mismatch_count": display_mismatches,
            "freshness_policy_seconds": 5,
            "safe_stop_policy_seconds": 15,
        }
        return {"summary": summary, "trace": trace}

    def _move_toward(self, point: dict[str, float], target: dict[str, float], speed: float) -> None:
        dx = target["x"] - point["x"]
        dy = target["y"] - point["y"]
        distance = (dx * dx + dy * dy) ** 0.5
        if distance == 0:
            return
        step = min(speed, distance)
        point["x"] += dx / distance * step
        point["y"] += dy / distance * step

    def ensure_session(self, mission_id: str, session_id: str) -> TruthSession:
        truth = self.store.get_truth(session_id)
        if truth:
            return truth
        truth = TruthSession(mission_id=mission_id, session_id=session_id)
        self.store.put_truth(truth)
        return truth

    def apply_tool(self, canonical_tool: str, request: ToolExecutionRequest) -> dict[str, Any]:
        if canonical_tool == "simulate_selective_message_drop":
            return self._simulate_selective_message_drop(request)
        if canonical_tool == "simulate_display_replay_effect":
            return self._simulate_display_replay_effect(request)
        if canonical_tool == "simulate_recovery_interference":
            return self._simulate_recovery_interference(request)
        if canonical_tool == "simulate_random_packet_loss":
            return self._simulate_random_packet_loss(request)
        if canonical_tool == "simulate_normal_baseline":
            return self._simulate_normal_baseline(request)
        if canonical_tool == "restore_attack_injection_state":
            return self._restore_attack_injection_state(request)
        if canonical_tool == "increase_monitoring_level":
            return self._increase_monitoring_level(request)
        if canonical_tool == "mark_telemetry_untrusted":
            return self._mark_telemetry_untrusted(request)
        if canonical_tool == "quarantine_link_session":
            return self._quarantine_link_session(request)
        if canonical_tool == "request_state_resynchronization":
            return self._request_state_resynchronization(request)
        if canonical_tool == "restore_validated_session":
            return self._restore_validated_session(request)
        raise ValueError(f"Unsupported canonical tool: {canonical_tool}")

    def _session_from_request(self, request: ToolExecutionRequest) -> TruthSession:
        mission_id = str(request.target.get("mission_id", "mission-alpha"))
        session_id = str(request.target.get("session_id", "session-17"))
        return self.ensure_session(mission_id, session_id)

    def _simulate_selective_message_drop(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        seed = int(request.input_data.get("seed", 42))
        duration = int(request.input_data.get("duration_seconds", 30))
        rng = random.Random(seed)
        drop_window = max(1, int(request.input_data.get("continuous_drop_window_seconds", duration)))
        drop_start = max(0, min(10, duration // 3))
        drop_end = min(duration, drop_start + drop_window - 1)
        mission = self.simulate_mission(
            truth.mission_id,
            truth.session_id,
            duration_seconds=max(duration, drop_end + 5),
            drop_start_second=drop_start,
            drop_end_second=drop_end,
        )
        summary = mission["summary"]
        affected = int(summary["dropped_reports"])
        max_gap = float(summary["max_consecutive_coordinate_gap_seconds"])
        non_target_impact_rate = round(rng.uniform(0.0, 0.02), 3)
        injection_id = new_id("inj-drop")

        truth.command_sent_count = int(summary["delivered_reports"] + summary["dropped_reports"])
        truth.command_delivered_count = int(summary["delivered_reports"])
        truth.last_command_id = f"cmd-{truth.command_sent_count:03d}"
        truth.last_vehicle_received_command_id = f"cmd-{truth.command_delivered_count:03d}"
        truth.ugv_state = str(summary["ugv_final_state"])
        truth.active_injections.append(injection_id)
        truth.updated_at = now_iso()
        self.store.put_truth(truth)

        return {
            "injection_id": injection_id,
            "affected_message_count": affected,
            "target_drop_rate": round(affected / max(truth.command_sent_count, 1), 2),
            "max_consecutive_gap_seconds": max_gap,
            "non_target_impact_rate": non_target_impact_rate,
            "mission_summary": summary,
            "mission_trace": mission["trace"],
            "truth_session": truth.model_dump(),
        }

    def _simulate_display_replay_effect(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        max_age = int(request.input_data.get("max_age_seconds", 20))
        stale_age = round(max_age * 0.71, 2)
        injection_id = new_id("inj-display")

        mission = self.simulate_mission(
            truth.mission_id,
            truth.session_id,
            duration_seconds=max(max_age, 20),
            replay_effect=True,
        )
        truth.telemetry_fresh = False
        truth.telemetry_untrusted = True
        truth.ugv_state = str(mission["summary"]["ugv_final_state"])
        truth.active_injections.append(injection_id)
        truth.updated_at = now_iso()
        self.store.put_truth(truth)

        return {
            "injection_id": injection_id,
            "displayed_count": max(1, int(max_age * 0.8)),
            "stale_age_seconds": stale_age,
            "payload_hash": f"sha256:{new_id('payload')}",
            "mission_summary": mission["summary"],
            "mission_trace": mission["trace"],
            "truth_session": truth.model_dump(),
        }

    def _simulate_recovery_interference(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        duration = int(request.input_data.get("duration_seconds", 30))
        block_seconds = min(30, max(1, int(request.input_data.get("block_seconds", 10))))
        injection_id = new_id("inj-recovery")
        mission = self.simulate_mission(
            truth.mission_id,
            truth.session_id,
            duration_seconds=max(duration, block_seconds + 20),
            drop_start_second=10,
            drop_end_second=10 + block_seconds - 1,
        )
        truth.command_sent_count = int(mission["summary"]["delivered_reports"] + mission["summary"]["dropped_reports"])
        truth.command_delivered_count = int(mission["summary"]["delivered_reports"])
        truth.last_command_id = f"cmd-{truth.command_sent_count:03d}"
        truth.last_vehicle_received_command_id = f"cmd-{truth.command_delivered_count:03d}"
        truth.ugv_state = str(mission["summary"]["ugv_final_state"])
        truth.active_injections.append(injection_id)
        truth.updated_at = now_iso()
        self.store.put_truth(truth)
        return {
            "injection_id": injection_id,
            "affected_message_count": int(mission["summary"]["dropped_reports"]),
            "target_drop_rate": round(float(mission["summary"]["dropped_reports"]) / max(truth.command_sent_count, 1), 2),
            "max_consecutive_gap_seconds": float(mission["summary"]["max_consecutive_coordinate_gap_seconds"]),
            "non_target_impact_rate": 0.01,
            "interfered_flow": "RECOVERY_OR_RESYNC",
            "bounded_to_e5": True,
            "mission_summary": mission["summary"],
            "mission_trace": mission["trace"],
            "truth_session": truth.model_dump(),
        }

    def _simulate_random_packet_loss(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        seed = int(request.input_data.get("seed", 42))
        rng = random.Random(seed)
        duration = int(request.input_data.get("duration_seconds", 30))
        sent = max(duration + 1, 1)
        dropped = max(1, round(sent * rng.uniform(0.01, 0.05)))
        mission = self.simulate_mission(truth.mission_id, truth.session_id, duration_seconds=duration)
        truth.command_sent_count = sent
        truth.command_delivered_count = sent - dropped
        truth.last_command_id = f"cmd-{sent:03d}"
        truth.last_vehicle_received_command_id = f"cmd-{sent - dropped:03d}"
        truth.telemetry_fresh = True
        truth.ugv_state = "MISSION_ACTIVE_WITH_MINOR_LOSS"
        truth.updated_at = now_iso()
        self.store.put_truth(truth)
        summary = dict(mission["summary"])
        summary.update({
            "dropped_reports": dropped,
            "max_consecutive_coordinate_gap_seconds": min(2, dropped),
            "loss_profile": "RANDOM_1_TO_5_PERCENT",
            "safe_stop_triggered": False,
            "ugv_final_state": truth.ugv_state,
        })
        return {
            "injection_id": None,
            "affected_message_count": dropped,
            "target_drop_rate": round(dropped / sent, 2),
            "max_consecutive_gap_seconds": float(summary["max_consecutive_coordinate_gap_seconds"]),
            "non_target_impact_rate": round(dropped / sent, 2),
            "fault_profile": "random_packet_loss",
            "mission_summary": summary,
            "mission_trace": mission["trace"],
            "truth_session": truth.model_dump(),
        }

    def _simulate_normal_baseline(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        duration = int(request.input_data.get("duration_seconds", 30))
        mission = self.simulate_mission(truth.mission_id, truth.session_id, duration_seconds=duration)
        truth.command_sent_count = duration + 1
        truth.command_delivered_count = duration + 1
        truth.last_command_id = f"cmd-{truth.command_sent_count:03d}"
        truth.last_vehicle_received_command_id = truth.last_command_id
        truth.telemetry_fresh = True
        truth.telemetry_untrusted = False
        truth.ugv_state = "MISSION_ACTIVE"
        truth.updated_at = now_iso()
        self.store.put_truth(truth)
        summary = dict(mission["summary"])
        summary.update({
            "baseline": True,
            "safe_stop_triggered": False,
            "max_consecutive_coordinate_gap_seconds": 0,
            "ugv_final_state": truth.ugv_state,
        })
        return {
            "injection_id": None,
            "affected_message_count": 0,
            "target_drop_rate": 0.0,
            "max_consecutive_gap_seconds": 0.0,
            "non_target_impact_rate": 0.0,
            "mission_summary": summary,
            "mission_trace": mission["trace"],
            "truth_session": truth.model_dump(),
        }

    def _restore_attack_injection_state(self, request: ToolExecutionRequest) -> dict[str, Any]:
        injection_id = str(request.target.get("injection_id", ""))
        restored = False
        remaining = 0
        touched: TruthSession | None = None

        for truth in self.store.list_truth_by_mission(str(request.target.get("mission_id", "mission-alpha"))):
            if injection_id in truth.active_injections:
                truth.active_injections.remove(injection_id)
                truth.command_delivered_count = truth.command_sent_count
                truth.last_vehicle_received_command_id = truth.last_command_id
                truth.telemetry_fresh = True
                truth.ugv_state = "MISSION_RECOVERED"
                truth.updated_at = now_iso()
                self.store.put_truth(truth)
                restored = True
                touched = truth
            remaining += len(truth.active_injections)

        return {
            "restored": restored,
            "remaining_injections": remaining,
            "truth_session": touched.model_dump() if touched else None,
        }

    def _increase_monitoring_level(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        truth.monitoring_level = "incident"
        truth.updated_at = now_iso()
        self.store.put_truth(truth)
        return {"monitoring_level": truth.monitoring_level, "truth_session": truth.model_dump()}

    def _mark_telemetry_untrusted(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        truth.telemetry_untrusted = True
        truth.updated_at = now_iso()
        self.store.put_truth(truth)
        return {"telemetry_untrusted": True, "truth_session": truth.model_dump()}

    def _quarantine_link_session(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        truth.quarantined = True
        truth.updated_at = now_iso()
        self.store.put_truth(truth)
        return {"quarantined": True, "non_target_unaffected": True, "truth_session": truth.model_dump()}

    def _request_state_resynchronization(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        active_attack = any(item.startswith(("inj-drop", "inj-recovery")) for item in truth.active_injections)
        if not active_attack:
            truth.command_delivered_count = truth.command_sent_count
            truth.last_vehicle_received_command_id = truth.last_command_id
            truth.telemetry_fresh = True
            truth.ugv_state = "MISSION_RECOVERED"
        truth.updated_at = now_iso()
        self.store.put_truth(truth)
        return {
            "state_converged": not active_attack,
            "blocked_by_active_injection": active_attack,
            "partial_success": active_attack,
            "recommended_transition": "SAFE_CONTAINMENT" if active_attack else "RECOVERED",
            "truth_session": truth.model_dump(),
        }

    def _restore_validated_session(self, request: ToolExecutionRequest) -> dict[str, Any]:
        truth = self._session_from_request(request)
        truth.quarantined = False
        truth.telemetry_untrusted = False
        truth.monitoring_level = "normal"
        if not truth.active_injections:
            truth.command_delivered_count = truth.command_sent_count
            truth.last_vehicle_received_command_id = truth.last_command_id
            truth.telemetry_fresh = True
            truth.ugv_state = "MISSION_RECOVERED"
        truth.updated_at = now_iso()
        self.store.put_truth(truth)
        return {"restored_session": True, "truth_session": truth.model_dump()}


class ToolExecutor:
    def __init__(self, store: Store, simulator: Simulator, registry: ToolRegistry) -> None:
        self.store = store
        self.simulator = simulator
        self.registry = registry

    def execute(self, request: ToolExecutionRequest, run_id: str | None = None) -> ToolExecutionResponse:
        duplicate = self.store.get_tool_by_idempotency(request.idempotency_key)
        if duplicate:
            response = ToolExecutionResponse(
                event_id=new_id("evt-duplicate"),
                incident_id=request.incident_id,
                tool_name=request.tool_name,
                policy_result="DUPLICATE_IGNORED",
                accepted=False,
                status="duplicate_ignored",
                audit_event_id=new_id("audit"),
                result={
                    "duplicate_of": duplicate["event_id"],
                    "previous_response": duplicate["response"],
                    "policy_gateway": self._policy_metadata("duplicate_ignored"),
                    "tool_registry": self.registry.metadata(),
                },
            )
            self._event(response, run_id, "tool_duplicate")
            return response

        contract = self.registry.get(request.tool_name)
        if not contract:
            response = self._denied(request, "tool_not_in_registry", self._policy_metadata("tool_not_in_registry"), None)
            self._persist(request, response, "denied")
            self._event(response, run_id, "tool_denied")
            return response

        canonical = self.registry.canonical_name(request.tool_name)
        assert canonical is not None
        final_risk = self._max_risk(request.risk_level, contract.risk_level)
        policy_decision = evaluate_action(canonical, final_risk, request.target)
        policy_decision.update(
            {
                "requested_tool_name": request.tool_name,
                "canonical_tool_name": canonical,
                "requested_risk_level": request.risk_level,
                "contract_risk_level": contract.risk_level,
                "final_risk_level": final_risk,
            }
        )
        if not policy_decision.get("allowed"):
            response = self._denied(request, str(policy_decision.get("reason") or "policy_gateway_denied"), policy_decision, contract)
            self._persist(request, response, "denied")
            self._event(response, run_id, "tool_denied")
            return response

        result = dict(self.simulator.apply_tool(canonical, request))
        result["policy_gateway"] = policy_decision
        result["tool_registry"] = self.registry.metadata(contract)
        response = ToolExecutionResponse(
            event_id=new_id("evt-tool-result"),
            incident_id=request.incident_id,
            tool_name=request.tool_name,
            policy_result="ALLOW",
            accepted=True,
            status="succeeded",
            audit_event_id=new_id("audit"),
            result=result,
        )
        self._persist(request, response, "succeeded")
        self._event(response, run_id, "tool_executed")
        return response

    def _max_risk(self, requested_risk: str, contract_risk: str) -> str:
        value = max(RISK_ORDER.get(requested_risk, 4), RISK_ORDER.get(contract_risk, 4))
        for level, rank in RISK_ORDER.items():
            if rank == value:
                return level
        return "A4"

    def _policy_metadata(self, reason: str) -> dict[str, Any]:
        return {
            "policy_version": POLICY_VERSION,
            "allowed": False,
            "reason": reason,
            "checked_boundaries": ["idempotency", "tool_registry", "risk_level", "target_allowlist", "safety_invariant"],
        }

    def _denied(
        self,
        request: ToolExecutionRequest,
        reason: str,
        policy_decision: dict[str, Any],
        contract: ToolContract | None,
    ) -> ToolExecutionResponse:
        return ToolExecutionResponse(
            event_id=new_id("evt-denied"),
            incident_id=request.incident_id,
            tool_name=request.tool_name,
            policy_result="ACTION_DENIED",
            accepted=False,
            status="denied",
            audit_event_id=new_id("audit"),
            result={
                "reason": reason,
                "policy_gateway": policy_decision,
                "tool_registry": self.registry.metadata(contract),
            },
        )

    def _persist(self, request: ToolExecutionRequest, response: ToolExecutionResponse, status: str) -> None:
        target_id = str(
            request.target.get("session_id")
            or request.target.get("injection_id")
            or request.target.get("mission_id")
            or "unknown"
        )
        self.store.put_tool_execution(
            execution_id=new_id("exec"),
            event_id=response.event_id,
            incident_id=request.incident_id,
            tool_name=request.tool_name,
            target_id=target_id,
            idempotency_key=request.idempotency_key,
            status=status,
            request=request.model_dump(by_alias=True),
            response=response.model_dump(),
        )

    def _event(self, response: ToolExecutionResponse, run_id: str | None, event_type: str) -> None:
        self.store.put_event(
            response.event_id,
            event_type,
            "single-tool-executor",
            response.model_dump(),
            run_id=run_id,
            incident_id=response.incident_id,
        )


class RedAgent:
    def build_plan(self, request: Any, incident_id: str) -> ToolExecutionRequest:
        if request.attack_type == "display_replay_effect":
            tool_name = "simulate_display_replay_effect"
            input_payload = {
                "inferred_role": "telemetry_candidate",
                "max_age_seconds": min(request.duration_seconds, 60),
                "duration_seconds": request.duration_seconds,
            }
            phase = "P3"
        elif request.attack_type == "recovery_interference":
            tool_name = "simulate_recovery_interference"
            input_payload = {
                "inferred_role": "recovery_flow_candidate",
                "block_seconds": min(request.duration_seconds, 30),
                "duration_seconds": request.duration_seconds,
                "seed": request.seed,
            }
            phase = "P2_E5"
        elif request.attack_type == "random_packet_loss":
            tool_name = "simulate_random_packet_loss"
            input_payload = {
                "fault_profile": "random_packet_loss",
                "duration_seconds": request.duration_seconds,
                "seed": request.seed,
            }
            phase = "E1"
        elif request.attack_type == "normal_baseline":
            tool_name = "simulate_normal_baseline"
            input_payload = {"duration_seconds": request.duration_seconds, "seed": request.seed}
            phase = "E0"
        else:
            tool_name = "simulate_selective_message_drop"
            input_payload = {
                "inferred_role": "coordinate_report_candidate",
                "inference_confidence": 0.82,
                "drop_mode": "CONTINUOUS_WINDOW",
                "continuous_drop_window_seconds": request.duration_seconds,
                "duration_seconds": request.duration_seconds,
                "seed": request.seed,
                "command_type": request.command_type,
            }
            phase = "P1"
        if getattr(request, "observation_text", ""):
            input_payload["observation_text_sha256_like"] = f"obs:{abs(hash(request.observation_text)) % 1000000:06d}"
            input_payload["observation_treated_as_untrusted"] = True
        return ToolExecutionRequest(
            event_id=new_id("evt-attack"),
            incident_id=incident_id,
            tool_name=tool_name,
            risk_level="A0" if tool_name == "simulate_normal_baseline" else "A1" if tool_name == "simulate_random_packet_loss" else "A2",
            idempotency_key=f"{incident_id}:{tool_name}:{request.session_id}:{phase}",
            target={
                "mission_id": request.mission_id,
                "session_id": request.session_id,
                "target_flow_id": request.target_flow_id,
            },
            input=input_payload,
            requested_by="red-agent",
        )


class Verifier:
    def __init__(self, store: Store) -> None:
        self.store = store

    def verify_impact(self, incident_id: str, attack_response: ToolExecutionResponse) -> VerificationResult:
        result = attack_response.result
        truth = result.get("truth_session") or {}
        gap = float(result.get("max_consecutive_gap_seconds", 0))
        success = bool(attack_response.accepted and (gap >= 5 or not truth.get("telemetry_fresh", True)))
        command_gap = truth.get("command_sent_count", 0) - truth.get("command_delivered_count", 0)
        mission_trace = result.get("mission_trace") or []
        first_anomaly_second = self._first_anomaly_second(mission_trace)
        mission_summary = result.get("mission_summary") or {}
        verification = VerificationResult(
            incident_id=incident_id,
            success=success,
            result={
                "command_gap": command_gap,
                "telemetry_fresh": truth.get("telemetry_fresh", True),
                "ugv_state": truth.get("ugv_state"),
                "max_consecutive_gap_seconds": gap,
                "target_drop_rate": result.get("target_drop_rate", 0),
                "non_target_impact_rate": result.get("non_target_impact_rate", 0),
                "fault_profile": result.get("fault_profile"),
                "technical_success": success,
                "first_anomaly_second": first_anomaly_second,
                "detection_threshold_second": None if first_anomaly_second is None else first_anomaly_second + 5,
                "safe_stop_second": mission_summary.get("safe_stop_second"),
            },
        )
        self.store.put_verification(verification.verification_id, incident_id, verification.success, verification.result)
        return verification

    def _first_anomaly_second(self, mission_trace: list[dict[str, Any]]) -> int | None:
        for item in mission_trace:
            gateway = item.get("gateway") or {}
            display = item.get("gcs_display") or {}
            if gateway.get("return_link_report_dropped") or display.get("display_truth_mismatch"):
                return int(item.get("t", 0))
        return None

    def verify_recovery(self, incident_id: str, truth: TruthSession) -> VerificationResult:
        recovered = (
            not truth.active_injections
            and not truth.quarantined
            and truth.command_delivered_count == truth.command_sent_count
            and truth.telemetry_fresh
        )
        verification = VerificationResult(
            incident_id=incident_id,
            success=recovered,
            result={
                "state_converged": recovered,
                "remaining_injections": len(truth.active_injections),
                "quarantined": truth.quarantined,
                "ugv_state": truth.ugv_state,
            },
        )
        self.store.put_verification(verification.verification_id, incident_id, verification.success, verification.result)
        return verification


class BlueAgent:
    def classify(self, impact: VerificationResult) -> ClassificationResult:
        command_gap = int(impact.result.get("command_gap", 0))
        telemetry_fresh = bool(impact.result.get("telemetry_fresh", True))
        max_gap = float(impact.result.get("max_consecutive_gap_seconds", 0))
        evidence: list[str] = []
        attack_score = 0.0
        fault_score = 0.1

        target_drop_rate = float(impact.result.get("target_drop_rate", 0))
        non_target_impact_rate = float(impact.result.get("non_target_impact_rate", 0))
        fault_profile = impact.result.get("fault_profile")

        if fault_profile == "random_packet_loss":
            fault_score += 0.65
            evidence.append("random low-rate packet loss profile observed")
        if command_gap > 0 and target_drop_rate >= 0.5:
            attack_score += 0.35
            evidence.append("specific command/report flow gap observed")
        elif command_gap > 0:
            fault_score += 0.15
            evidence.append("minor delivery gap below attack selectivity threshold")
        if max_gap >= 15:
            attack_score += 0.25
            evidence.append("coordinate freshness gap crossed safe-stop threshold")
        if command_gap > 0 and max_gap > 0 and target_drop_rate >= 0.5:
            attack_score += 0.2
            evidence.append("gateway/vehicle delivery mismatch in mock truth state")
        if not telemetry_fresh:
            attack_score += 0.2
            evidence.append("telemetry stale/replay suspected")
        if command_gap > 0 and non_target_impact_rate <= 0.05 and target_drop_rate >= 0.5:
            attack_score += 0.15
            evidence.append("single session affected")

        if command_gap == 0 and telemetry_fresh:
            fault_score += 0.3
            evidence.append("no material anomaly")

        if attack_score >= 0.85:
            classification = "ATTACK_CONFIRMED"
        elif attack_score >= 0.70:
            classification = "ATTACK_SUSPECTED"
        elif fault_score >= 0.70:
            classification = "FAULT_SUSPECTED"
        elif abs(attack_score - fault_score) < 0.15:
            classification = "UNCERTAIN"
        else:
            classification = "WATCH"

        return ClassificationResult(
            classification=classification,
            attack_score=round(attack_score, 2),
            fault_score=round(fault_score, 2),
            evidence=evidence,
        )

    def plan(self, classification: ClassificationResult, mission_id: str, session_id: str, incident_id: str) -> DefensePlan:
        actions: list[ToolExecutionRequest] = []
        if classification.classification in {"ATTACK_SUSPECTED", "ATTACK_CONFIRMED", "UNCERTAIN"}:
            actions.extend(
                [
                    self._tool(incident_id, "increase_monitoring_level", mission_id, session_id, "monitor"),
                    self._tool(incident_id, "mark_telemetry_untrusted", mission_id, session_id, "stale"),
                    self._tool(incident_id, "quarantine_link_session", mission_id, session_id, "quarantine"),
                    self._tool(incident_id, "request_state_resynchronization", mission_id, session_id, "resync"),
                ]
            )
        return DefensePlan(
            classification=classification.classification,
            actions=actions,
            rationale="least-impact response: raise monitoring, mark untrusted data, isolate target session, then resync",
        )

    def _tool(self, incident_id: str, tool_name: str, mission_id: str, session_id: str, phase: str) -> ToolExecutionRequest:
        risk = "A1" if tool_name in {"increase_monitoring_level", "mark_telemetry_untrusted"} else "A2"
        return ToolExecutionRequest(
            event_id=new_id("evt-defense"),
            incident_id=incident_id,
            tool_name=tool_name,
            risk_level=risk,
            idempotency_key=f"{incident_id}:{tool_name}:{session_id}:{phase}",
            target={"mission_id": mission_id, "session_id": session_id},
            input={},
            requested_by="blue-agent",
        )


class Judge:
    def __init__(self, store: Store) -> None:
        self.store = store

    def judge(
        self,
        incident_id: str,
        mission_id: str,
        attack: ToolExecutionResponse,
        classification: ClassificationResult,
        defense_results: list[ToolExecutionResponse],
        recovery: VerificationResult,
        truth: TruthSession,
    ) -> JudgeVerdict:
        attack_score = 0
        labels: list[str] = []
        reason: dict[str, Any] = {}

        scenario_is_attack = attack.tool_name not in {"simulate_random_packet_loss", "simulate_normal_baseline"}
        if scenario_is_attack and attack.accepted and attack.result.get("affected_message_count", 0) > 0:
            attack_score += 30
            labels.append("ATTACK_TECH_SUCCESS")
        if scenario_is_attack and (attack.result.get("max_consecutive_gap_seconds", 0) >= 15 or not truth.telemetry_fresh):
            attack_score += 20
            labels.append("ATTACK_MISSION_SUCCESS")
        if scenario_is_attack and attack.result.get("max_consecutive_gap_seconds", 0) >= 10:
            attack_score += 20
        if scenario_is_attack and not truth.active_injections:
            attack_score += 10
        if not scenario_is_attack:
            labels.append("BASELINE_OR_FAULT_PROFILE")

        defense_score = 0
        if classification.classification in {"ATTACK_SUSPECTED", "ATTACK_CONFIRMED"}:
            defense_score += 25
            labels.append("DEFENSE_DETECTED")
        if classification.attack_score >= 0.70:
            defense_score += 20
        if any(item.tool_name == "quarantine_link_session" and item.accepted for item in defense_results):
            defense_score += 20
            labels.append("DEFENSE_CONTAINED")
        if recovery.success:
            defense_score += 35
            labels.append("DEFENSE_RECOVERED")

        availability = 100
        if any(item.policy_result == "DUPLICATE_IGNORED" for item in defense_results):
            availability -= 15
        if truth.quarantined:
            availability -= 20
        if not recovery.success:
            availability -= 15
        availability = max(0, availability)
        total_score = round((attack_score + defense_score) * (availability / 100), 2)

        attack_truth = attack.result.get("truth_session") or {}
        max_gap = float(attack.result.get("max_consecutive_gap_seconds", 0))
        judge_audit_event = {
            "event_id": new_id("judge-audit"),
            "incident_id": incident_id,
            "mission_id": mission_id,
            "scenario_path": self._scenario_path(attack.tool_name),
            "technical_success": {
                "target_drop_rate": attack.result.get("target_drop_rate", 0),
                "non_target_impact_rate": attack.result.get("non_target_impact_rate", 0),
                "max_consecutive_coordinate_gap_seconds": max_gap,
                "passed": bool(scenario_is_attack and attack.accepted and attack.result.get("affected_message_count", 0) > 0),
            },
            "mission_success": {
                "ugv_state": attack_truth.get("ugv_state"),
                "safe_stop_second": (attack.result.get("mission_summary") or {}).get("safe_stop_second"),
                "safe_stop_duration_seconds": max(0, round(max_gap - 5, 2)),
                "passed": attack_truth.get("ugv_state") == "SAFE_STOP_CAUSED_BY_COORD_STALE" or not attack_truth.get("telemetry_fresh", True),
            },
            "mission_simulation": attack.result.get("mission_summary"),
            "safety": {
                "boundary_violation_count": 0,
                "remaining_injections": len(truth.active_injections),
                "passed": len(truth.active_injections) == 0,
            },
            "final_verdict": "ATTACK_SUCCESS_CANDIDATE" if attack_score >= 50 else "BASELINE_OR_FAULT" if not scenario_is_attack else "INCONCLUSIVE",
        }
        reason.update(
            {
                "attack_tool": attack.tool_name,
                "classification": classification.model_dump(),
                "defense_tool_count": len(defense_results),
                "remaining_injections": len(truth.active_injections),
                "score_formula": "(attack_score + defense_score) * availability/100",
                "judge_audit_event": judge_audit_event,
            }
        )
        verdict = JudgeVerdict(
            incident_id=incident_id,
            mission_id=mission_id,
            attack_score=attack_score,
            defense_score=defense_score,
            availability=availability,
            total_score=total_score,
            labels=labels or ["INCONCLUSIVE"],
            reason=reason,
        )
        self.store.put_judge_verdict(verdict)
        return verdict

    def _scenario_path(self, tool_name: str) -> str:
        if tool_name in {"simulate_selective_message_drop", "simulate_selective_command_drop"}:
            return "P1"
        if tool_name == "simulate_recovery_interference":
            return "P2_E5"
        if tool_name == "simulate_display_replay_effect":
            return "P3"
        if tool_name == "simulate_random_packet_loss":
            return "E1_FAULT"
        if tool_name == "simulate_normal_baseline":
            return "E0_BASELINE"
        return "UNKNOWN"


class OpenAIPlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def plan(self, incident: dict[str, Any], allow_fallback: bool) -> LlmPlanResponse:
        fallback = self._fallback_if_unconfigured(incident, allow_fallback)
        if fallback:
            return fallback
        async with httpx.AsyncClient(timeout=self.settings.openai_timeout_seconds) as client:
            response = await client.post(
                self.settings.openai_responses_url,
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                json=self._payload(incident),
            )
            response.raise_for_status()
        return self._response_from_openai(response.json())

    def plan_sync(self, incident: dict[str, Any], allow_fallback: bool) -> LlmPlanResponse:
        fallback = self._fallback_if_unconfigured(incident, allow_fallback)
        if fallback:
            return fallback
        with httpx.Client(timeout=self.settings.openai_timeout_seconds) as client:
            response = client.post(
                self.settings.openai_responses_url,
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                json=self._payload(incident),
            )
            response.raise_for_status()
        return self._response_from_openai(response.json())

    def _fallback_if_unconfigured(self, incident: dict[str, Any], allow_fallback: bool) -> LlmPlanResponse | None:
        if self.settings.openai_configured:
            return None
        if not allow_fallback:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        return LlmPlanResponse(openai_used=False, source="rule_fallback_no_openai_key", plan=self._fallback(incident))

    def _payload(self, incident: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "classification": {"type": "string"},
                "recommended_actions": {"type": "array", "items": {"type": "string"}},
                "rationale": {"type": "string"},
                "safety_notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["classification", "recommended_actions", "rationale", "safety_notes"],
        }
        return {
            "model": self.settings.openai_model,
            "reasoning": {"effort": self.settings.openai_reasoning_effort},
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You produce typed advisory plans only. Do not execute tools. "
                                "Only recommend actions from the Tool Registry when needed. "
                                "Never access real UAV, RF, shell, SQL, cloud IAM, or external attack targets. "
                                "Keep rationale concise and cite which observed evidence changed the recommendation."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps({"incident": incident}, sort_keys=True),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "dah_agent_plan",
                    "schema": schema,
                    "strict": True,
                }
            },
        }

    def _response_from_openai(self, data: dict[str, Any]) -> LlmPlanResponse:
        text = self._extract_output_text(data)
        plan = json.loads(text)
        return LlmPlanResponse(openai_used=True, source="openai_responses_api", plan=plan)

    def _fallback(self, incident: dict[str, Any], rationale: str | None = None) -> dict[str, Any]:
        return {
            "classification": incident.get("classification", "UNCERTAIN"),
            "recommended_actions": ["increase_monitoring_level", "mark_telemetry_untrusted"],
            "rationale": rationale or "OpenAI API key is not configured; returned deterministic conservative fallback.",
            "safety_notes": ["No state change is executed by the LLM path."],
        }

    def _extract_output_text(self, data: dict[str, Any]) -> str:
        if data.get("output_text"):
            return str(data["output_text"])
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    return str(content["text"])
        raise ValueError("OpenAI response did not include output text")


class ScenarioRunner:
    def __init__(
        self,
        store: Store,
        simulator: Simulator,
        executor: ToolExecutor,
        red: RedAgent,
        blue: BlueAgent,
        verifier: Verifier,
        judge: Judge,
        langgraph_adapter: LangGraphAgentAdapter | None = None,
        openai_planner: OpenAIPlanner | None = None,
    ) -> None:
        self.store = store
        self.simulator = simulator
        self.executor = executor
        self.red = red
        self.blue = blue
        self.verifier = verifier
        self.judge = judge
        self.langgraph_adapter = langgraph_adapter or LangGraphAgentAdapter()
        self.openai_planner = openai_planner

    def run(self, request: Any) -> dict[str, Any]:
        run_id = new_id("run")
        incident_id = new_id("inc")
        self.store.put_incident(incident_id, "running")
        truth = self.simulator.ensure_session(request.mission_id, request.session_id)
        red_graph = self.langgraph_adapter.run_red_graph(request, incident_id)
        self.store.put_event(
            new_id("evt-langgraph-red"),
            "langgraph_red_trace",
            "langgraph-adapter",
            red_graph,
            run_id=run_id,
            incident_id=incident_id,
        )
        self.store.put_event(
            new_id("evt-baseline"),
            "baseline_ready",
            "mock-simulator",
            truth.model_dump(),
            run_id=run_id,
            incident_id=incident_id,
        )

        red_plan = self.red.build_plan(request, incident_id)
        if getattr(request, "observation_text", ""):
            self.store.put_event(
                new_id("evt-trust-boundary"),
                "prompt_input_sanitized",
                "input-trust-boundary",
                {
                    "observation_treated_as_untrusted": True,
                    "raw_instruction_ignored": True,
                    "retained_fields": ["mission_id", "session_id", "flow metadata", "hash"],
                },
                run_id=run_id,
                incident_id=incident_id,
            )
        self.store.put_event(
            red_plan.event_id,
            "red_plan_created",
            "red-agent",
            red_plan.model_dump(by_alias=True),
            run_id=run_id,
            incident_id=incident_id,
        )
        attack_result = self.executor.execute(red_plan, run_id=run_id)
        impact = self.verifier.verify_impact(incident_id, attack_result)
        if impact.result.get("command_gap", 0) > 0:
            self.store.put_event(
                new_id("evt-command-delivery-anomaly"),
                "command_delivery_anomaly",
                "multi-source-evidence-layer",
                {
                    "command_gap": impact.result.get("command_gap"),
                    "target_drop_rate": impact.result.get("target_drop_rate"),
                    "max_consecutive_gap_seconds": impact.result.get("max_consecutive_gap_seconds"),
                    "fault_profile": impact.result.get("fault_profile"),
                },
                run_id=run_id,
                incident_id=incident_id,
            )
        self.store.put_event(
            impact.verification_id,
            "impact_verified",
            "verification-layer",
            impact.model_dump(),
            run_id=run_id,
            incident_id=incident_id,
        )

        classification = self.blue.classify(impact)
        self.store.put_event(
            new_id("evt-classification"),
            "classification_completed",
            "blue-agent",
            classification.model_dump(),
            run_id=run_id,
            incident_id=incident_id,
        )
        defense_plan = self.blue.plan(classification, request.mission_id, request.session_id, incident_id)
        llm_plan = self._llm_advisory_plan(
            request,
            run_id,
            incident_id,
            attack_result,
            impact,
            classification,
            defense_plan,
        )
        blue_graph = self.langgraph_adapter.run_blue_graph(impact, classification, defense_plan)
        self.store.put_event(
            new_id("evt-langgraph-blue"),
            "langgraph_blue_trace",
            "langgraph-adapter",
            blue_graph,
            run_id=run_id,
            incident_id=incident_id,
        )
        graph_consistency = self._langgraph_consistency(red_graph, blue_graph, red_plan, defense_plan)
        self.store.put_event(
            new_id("evt-langgraph-consistency"),
            "langgraph_consistency_checked",
            "langgraph-adapter",
            graph_consistency,
            run_id=run_id,
            incident_id=incident_id,
        )
        agent_graph = self.langgraph_adapter.combine(red_graph, blue_graph, llm_plan)
        agent_graph["consistency"] = graph_consistency
        agent_graph.setdefault("decisions", {})["langgraph_consistent"] = graph_consistency["consistent"]
        defense_results = [self.executor.execute(action, run_id=run_id) for action in defense_plan.actions]
        if any(item.result.get("recommended_transition") == "SAFE_CONTAINMENT" for item in defense_results):
            self.store.put_event(
                new_id("evt-safe-containment"),
                "safe_containment_entered",
                "blue-recovery-manager",
                {
                    "reason": "resynchronization blocked by active injection",
                    "compensation": "restore_attack_injection_state_then_restore_validated_session",
                    "max_duration_seconds": 30,
                },
                run_id=run_id,
                incident_id=incident_id,
            )

        injection_id = str(attack_result.result.get("injection_id", ""))
        restore_request = ToolExecutionRequest(
            event_id=new_id("evt-restore"),
            incident_id=incident_id,
            tool_name="restore_attack_injection_state",
            risk_level="A2",
            idempotency_key=f"{incident_id}:restore_attack_injection_state:{injection_id}:CLOSED",
            target={"mission_id": request.mission_id, "injection_id": injection_id},
            input={},
            requested_by="red-restore-manager",
        )
        restore_result = self.executor.execute(restore_request, run_id=run_id)

        final_restore = ToolExecutionRequest(
            event_id=new_id("evt-defense"),
            incident_id=incident_id,
            tool_name="restore_validated_session",
            risk_level="A2",
            idempotency_key=f"{incident_id}:restore_validated_session:{request.session_id}:recovered",
            target={"mission_id": request.mission_id, "session_id": request.session_id},
            input={},
            requested_by="blue-recovery-manager",
        )
        defense_results.append(self.executor.execute(final_restore, run_id=run_id))

        final_truth = self.simulator.ensure_session(request.mission_id, request.session_id)
        recovery = self.verifier.verify_recovery(incident_id, final_truth)
        self.store.put_event(
            recovery.verification_id,
            "recovery_verified",
            "verification-layer",
            recovery.model_dump(),
            run_id=run_id,
            incident_id=incident_id,
        )

        verdict = self.judge.judge(
            incident_id,
            request.mission_id,
            attack_result,
            classification,
            defense_results,
            recovery,
            final_truth,
        )
        self.store.put_event(
            verdict.verdict_id,
            "judge_verdict",
            "independent-judge",
            verdict.model_dump(),
            run_id=run_id,
            incident_id=incident_id,
        )
        events = self.store.list_events(run_id)
        response = {
            "run_id": run_id,
            "incident_id": incident_id,
            "status": "completed",
            "red_plan": red_plan.model_dump(by_alias=True),
            "attack_result": attack_result.model_dump(),
            "impact_verification": impact.model_dump(),
            "classification": classification.model_dump(),
            "defense_plan": defense_plan.model_dump(by_alias=True),
            "defense_results": [item.model_dump() for item in defense_results],
            "restore_result": restore_result.model_dump(),
            "recovery_verification": recovery.model_dump(),
            "judge_verdict": verdict.model_dump(),
            "truth_state": final_truth.model_dump(),
            "mission_simulation": {
                "summary": attack_result.result.get("mission_summary"),
                "trace": attack_result.result.get("mission_trace", []),
            },
            "safety_transitions": [event for event in events if event["event_type"] in {"safe_containment_entered"}],
            "llm_plan": llm_plan,
            "agent_graph": agent_graph,
            "events": events,
        }
        evidence_records = build_evidence_ledger(response, events)
        for record in evidence_records:
            self.store.put_evidence_record(record)
        response["evidence_ledger_records"] = evidence_records
        self.store.put_incident(
            incident_id,
            "completed",
            classification.classification,
            classification.attack_score,
            classification.fault_score,
            closed_at=now_iso(),
        )
        self.store.put_run(run_id, incident_id, "completed", request.model_dump(), response)
        return response


    def _langgraph_consistency(
        self,
        red_graph: dict[str, Any],
        blue_graph: dict[str, Any],
        red_plan: ToolExecutionRequest,
        defense_plan: DefensePlan,
    ) -> dict[str, Any]:
        red_decisions = red_graph.get("decisions", {})
        blue_decisions = blue_graph.get("decisions", {})
        graph_red_tool = red_decisions.get("red_selected_tool")
        actual_red_tool = self.executor.registry.canonical_name(red_plan.tool_name) or red_plan.tool_name
        graph_actions = [str(item) for item in blue_decisions.get("blue_defense_actions", [])]
        actual_actions = [item.tool_name for item in defense_plan.actions]
        red_tool_match = graph_red_tool == actual_red_tool
        blue_actions_match = graph_actions == actual_actions
        return {
            "consistent": bool(red_tool_match and blue_actions_match),
            "red_tool_match": red_tool_match,
            "blue_actions_match": blue_actions_match,
            "graph_red_tool": graph_red_tool,
            "actual_red_tool": actual_red_tool,
            "graph_defense_actions": graph_actions,
            "actual_defense_actions": actual_actions,
            "adapter_role": "trace_consistency_check",
            "execution_source": "local_or_temporal_runner",
        }


    def _llm_advisory_plan(
        self,
        request: Any,
        run_id: str,
        incident_id: str,
        attack_result: ToolExecutionResponse,
        impact: VerificationResult,
        classification: ClassificationResult,
        defense_plan: DefensePlan,
    ) -> dict[str, Any] | None:
        use_llm_advisory = bool(getattr(request, "use_llm_advisory", True))
        legacy_flag = getattr(request, "use_llm_for_uncertain", None)
        if legacy_flag is False:
            use_llm_advisory = False
        if not use_llm_advisory:
            return None
        incident = {
            "incident_id": incident_id,
            "mission_id": request.mission_id,
            "session_id": request.session_id,
            "attack_type": request.attack_type,
            "classification": classification.classification,
            "attack_score": classification.attack_score,
            "fault_score": classification.fault_score,
            "classification_evidence": classification.evidence,
            "impact_result": impact.result,
            "red_tool": attack_result.tool_name,
            "red_tool_policy_result": attack_result.policy_result,
            "planned_defense_actions": [item.tool_name for item in defense_plan.actions],
            "tool_registry": self.executor.registry.names(),
            "llm_role": "advisory_typed_plan_only",
            "execution_boundary": "LLM output is audited but never executes tools directly.",
        }
        settings = self.openai_planner.settings if self.openai_planner else None
        self.store.put_event(
            new_id("evt-llm-plan-requested"),
            "llm_plan_requested",
            "llm-advisory-planner",
            {
                "incident_id": incident_id,
                "model": settings.openai_model if settings else None,
                "base_url": settings.openai_base_url if settings else None,
                "openai_configured": settings.openai_configured if settings else False,
                "trigger": "scenario_run_advisory",
                "applied_to_execution": False,
                "incident_summary": {
                    "classification": classification.classification,
                    "attack_score": classification.attack_score,
                    "fault_score": classification.fault_score,
                    "impact_success": impact.success,
                    "red_tool": attack_result.tool_name,
                },
            },
            run_id=run_id,
            incident_id=incident_id,
        )
        try:
            if not self.openai_planner:
                raise RuntimeError("OpenAI planner is not attached to ScenarioRunner")
            response = self.openai_planner.plan_sync(incident, allow_fallback=True)
        except Exception as exc:
            fallback_plan = (
                self.openai_planner._fallback(  # Internal fallback keeps scenario execution non-blocking.
                    incident,
                    rationale=f"OpenAI call failed during scenario advisory planning; conservative fallback used: {exc}",
                )
                if self.openai_planner
                else {
                    "classification": classification.classification,
                    "recommended_actions": ["increase_monitoring_level", "mark_telemetry_untrusted"],
                    "rationale": f"OpenAI planner unavailable; conservative fallback used: {exc}",
                    "safety_notes": ["No state change is executed by the LLM path."],
                }
            )
            response = LlmPlanResponse(
                openai_used=False,
                source="rule_fallback_after_openai_error",
                plan=fallback_plan,
                error=str(exc),
            )
        payload = response.model_dump()
        if settings:
            payload["model"] = settings.openai_model
            payload["base_url"] = settings.openai_base_url
        payload["applied_to_execution"] = False
        recommended = [str(item) for item in payload.get("plan", {}).get("recommended_actions", [])]
        allowed = [item for item in recommended if self.executor.registry.canonical_name(item)]
        denied = [item for item in recommended if item not in allowed]
        payload["recommended_actions_allowed"] = allowed
        payload["recommended_actions_denied"] = denied
        self.store.put_event(
            new_id("evt-llm-plan-completed"),
            "llm_plan_completed",
            "llm-advisory-planner",
            payload,
            run_id=run_id,
            incident_id=incident_id,
        )
        return payload


class BatchExperimentRunner:
    def __init__(self, store: Store, scenario_runner: ScenarioRunner) -> None:
        self.store = store
        self.scenario_runner = scenario_runner

    def run_batch(self, request: BatchExperimentRequest) -> dict[str, Any]:
        experiment_id = new_id("exp")
        rows: list[dict[str, Any]] = []
        run_ids: list[str] = []
        for index in range(request.runs):
            scenario_request = ScenarioRunRequest(
                mission_id=request.mission_id,
                session_id=f"{request.session_id_prefix}-{index + 1:03d}",
                attack_type=request.attack_type,
                target_flow_id=request.target_flow_id,
                command_type=request.command_type,
                duration_seconds=request.duration_seconds,
                seed=request.seed_start + index,
                use_llm_advisory=request.use_llm_advisory,
            )
            result = self.scenario_runner.run(scenario_request)
            verdict = result["judge_verdict"]
            classification = result["classification"]
            run_ids.append(result["run_id"])
            scenario_is_attack = scenario_request.attack_type not in {"random_packet_loss", "normal_baseline"}
            detected = classification["classification"] in {"ATTACK_SUSPECTED", "ATTACK_CONFIRMED"}
            impact_result = result["impact_verification"]["result"]
            summary = (result.get("mission_simulation") or {}).get("summary") or {}
            first_anomaly = impact_result.get("first_anomaly_second")
            detection_threshold = impact_result.get("detection_threshold_second")
            rows.append(
                {
                    "run_id": result["run_id"],
                    "incident_id": result["incident_id"],
                    "seed": scenario_request.seed,
                    "attack_type": scenario_request.attack_type,
                    "classification": classification["classification"],
                    "attack_score": verdict["attack_score"],
                    "defense_score": verdict["defense_score"],
                    "availability": verdict["availability"],
                    "total_score": verdict["total_score"],
                    "recovery_success": result["recovery_verification"]["success"],
                    "false_positive": (not scenario_is_attack) and detected,
                    "false_negative": scenario_is_attack and not detected,
                    "detection_latency_seconds": None if first_anomaly is None or not detected else max(0, detection_threshold - first_anomaly),
                    "mitigation_latency_seconds": 0 if not detected else len(result["defense_results"]),
                    "recovery_time_seconds": 0 if not scenario_is_attack else max(0, (summary.get("safe_stop_second") or request.duration_seconds) - (first_anomaly or 0)),
                    "safe_stop_triggered": bool(summary.get("safe_stop_triggered")),
                    "labels": "|".join(verdict["labels"]),
                }
            )
        metrics = self._metrics(rows)
        csv_text = self._csv(rows)
        payload = {
            "experiment_id": experiment_id,
            "status": "completed",
            "run_count": len(rows),
            "run_ids": run_ids,
            "metrics": metrics,
            "csv": csv_text,
        }
        self.store.put_event(
            experiment_id,
            "batch_experiment_completed",
            "batch-experiment-runner",
            payload,
            run_id=experiment_id,
            incident_id=experiment_id,
        )
        return payload

    def _metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        count = max(len(rows), 1)
        detected = sum(1 for row in rows if row["classification"] in {"ATTACK_SUSPECTED", "ATTACK_CONFIRMED"})
        recovered = sum(1 for row in rows if row["recovery_success"])
        attack_rows = [row for row in rows if row["attack_type"] not in {"random_packet_loss", "normal_baseline"}]
        non_attack_rows = [row for row in rows if row["attack_type"] in {"random_packet_loss", "normal_baseline"}]
        latencies = [row["detection_latency_seconds"] for row in rows if row["detection_latency_seconds"] is not None]
        mitigation = [row["mitigation_latency_seconds"] for row in rows]
        recovery_times = [row["recovery_time_seconds"] for row in rows]
        return {
            "attack_detection_rate": round(detected / count, 4),
            "recovery_success_rate": round(recovered / count, 4),
            "false_positive_rate": round(sum(1 for row in non_attack_rows if row["false_positive"]) / max(len(non_attack_rows), 1), 4),
            "false_negative_rate": round(sum(1 for row in attack_rows if row["false_negative"]) / max(len(attack_rows), 1), 4),
            "average_detection_latency_seconds": round(sum(latencies) / max(len(latencies), 1), 2),
            "average_mitigation_latency_seconds": round(sum(mitigation) / count, 2),
            "average_recovery_time_seconds": round(sum(recovery_times) / count, 2),
            "safe_stop_rate": round(sum(1 for row in rows if row["safe_stop_triggered"]) / count, 4),
            "average_attack_score": round(sum(row["attack_score"] for row in rows) / count, 2),
            "average_defense_score": round(sum(row["defense_score"] for row in rows) / count, 2),
            "average_availability": round(sum(row["availability"] for row in rows) / count, 2),
            "average_total_score": round(sum(row["total_score"] for row in rows) / count, 2),
            "sample_size": len(rows),
            "baseline_insufficient": len(non_attack_rows) > 0 and len(non_attack_rows) < 30,
        }

    def _csv(self, rows: list[dict[str, Any]]) -> str:
        headers = [
            "run_id",
            "incident_id",
            "seed",
            "classification",
            "attack_score",
            "defense_score",
            "availability",
            "total_score",
            "recovery_success",
            "labels",
            "attack_type",
            "false_positive",
            "false_negative",
            "detection_latency_seconds",
            "mitigation_latency_seconds",
            "recovery_time_seconds",
            "safe_stop_triggered",
        ]
        lines = [",".join(headers)]
        for row in rows:
            lines.append(",".join(str(row[header]) for header in headers))
        return "\n".join(lines) + "\n"


class ExperimentSuiteRunner:
    GROUPS = [
        ("E0", "normal_baseline", "normal operation baseline"),
        ("E1", "random_packet_loss", "normal low-rate random packet loss"),
        ("E2", "selective_message_drop", "single attack tool causal check"),
        ("E3", "selective_message_drop", "full P1 attack without external simulator defense split"),
        ("E4", "selective_message_drop", "P1 attack plus built-in Blue response"),
        ("E5", "recovery_interference", "bounded P2 recovery interference"),
    ]

    def __init__(self, store: Store, batch_runner: BatchExperimentRunner) -> None:
        self.store = store
        self.batch_runner = batch_runner

    def run_suite(self, request: ExperimentSuiteRequest) -> dict[str, Any]:
        suite_id = new_id("suite")
        groups: list[dict[str, Any]] = []
        for offset, (group_id, attack_type, purpose) in enumerate(self.GROUPS):
            batch = self.batch_runner.run_batch(
                BatchExperimentRequest(
                    mission_id=request.mission_id,
                    session_id_prefix=f"{request.session_id_prefix}-{group_id.lower()}",
                    attack_type=attack_type,
                    duration_seconds=request.duration_seconds,
                    runs=request.runs_per_group,
                    seed_start=request.seed_start + offset * 1000,
                    use_llm_advisory=request.use_llm_advisory,
                )
            )
            groups.append(
                {
                    "group_id": group_id,
                    "attack_type": attack_type,
                    "purpose": purpose,
                    "experiment_id": batch["experiment_id"],
                    "run_count": batch["run_count"],
                    "metrics": batch["metrics"],
                    "run_ids": batch["run_ids"],
                }
            )
        attack_groups = [group for group in groups if group["group_id"] in {"E2", "E3", "E4", "E5"}]
        fault_groups = [group for group in groups if group["group_id"] in {"E0", "E1"}]
        aggregate = {
            "group_count": len(groups),
            "total_runs": sum(group["run_count"] for group in groups),
            "attack_group_detection_rate_avg": round(
                sum(group["metrics"]["attack_detection_rate"] for group in attack_groups) / max(len(attack_groups), 1), 4
            ),
            "attack_group_false_negative_rate_avg": round(
                sum(group["metrics"]["false_negative_rate"] for group in attack_groups) / max(len(attack_groups), 1), 4
            ),
            "fault_group_false_positive_rate_avg": round(
                sum(group["metrics"]["attack_detection_rate"] for group in fault_groups) / max(len(fault_groups), 1), 4
            ),
            "average_detection_latency_seconds": round(
                sum(group["metrics"]["average_detection_latency_seconds"] for group in groups) / max(len(groups), 1), 2
            ),
            "average_recovery_time_seconds": round(
                sum(group["metrics"]["average_recovery_time_seconds"] for group in groups) / max(len(groups), 1), 2
            ),
            "average_total_score": round(
                sum(group["metrics"]["average_total_score"] for group in groups) / max(len(groups), 1), 2
            ),
        }
        payload = {"suite_id": suite_id, "status": "completed", "groups": groups, "aggregate": aggregate}
        self.store.put_event(
            suite_id,
            "experiment_suite_completed",
            "experiment-suite-runner",
            payload,
            run_id=suite_id,
            incident_id=suite_id,
        )
        return payload


class ReplayRunner:
    def __init__(self, store: Store, scenario_runner: ScenarioRunner) -> None:
        self.store = store
        self.scenario_runner = scenario_runner

    def replay(self, source_run_id: str) -> dict[str, Any]:
        source = self.store.get_run(source_run_id)
        if not source:
            raise KeyError(source_run_id)
        replay_result = self.scenario_runner.run(ScenarioRunRequest.model_validate(source["request"]))
        source_response = source["response"]
        source_policy = self._policy_signature(source_response)
        replay_policy = self._policy_signature(replay_result)
        source_judge = self._judge_signature(source_response)
        replay_judge = self._judge_signature(replay_result)
        policy_match = source_policy == replay_policy
        judge_match = source_judge == replay_judge
        payload = {
            "source_run_id": source_run_id,
            "replay_run_id": replay_result["run_id"],
            "policy_match": policy_match,
            "judge_match": judge_match,
            "deterministic": policy_match and judge_match,
            "comparison": {
                "source_policy": source_policy,
                "replay_policy": replay_policy,
                "source_judge": source_judge,
                "replay_judge": replay_judge,
            },
        }
        self.store.put_event(
            new_id("evt-replay"),
            "replay_completed",
            "replay-harness",
            payload,
            run_id=replay_result["run_id"],
            incident_id=replay_result["incident_id"],
        )
        self.store.put_replay_run(new_id("replay"), source_response["incident_id"], payload["deterministic"], payload)
        return payload

    def _policy_signature(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        tool_results = [response["attack_result"], *response["defense_results"], response["restore_result"]]
        return [
            {
                "tool_name": item["tool_name"],
                "policy_result": item["policy_result"],
                "accepted": item["accepted"],
                "status": item["status"],
            }
            for item in tool_results
        ]

    def _judge_signature(self, response: dict[str, Any]) -> dict[str, Any]:
        verdict = response["judge_verdict"]
        return {
            "attack_score": verdict["attack_score"],
            "defense_score": verdict["defense_score"],
            "availability": verdict["availability"],
            "total_score": verdict["total_score"],
            "labels": verdict["labels"],
            "final_verdict": verdict["reason"].get("judge_audit_event", {}).get("final_verdict"),
        }


class ReportGenerator:
    def __init__(self, store: Store) -> None:
        self.store = store

    def build(self, run_id: str) -> dict[str, Any]:
        row = self.store.get_run(run_id)
        if not row:
            raise KeyError(run_id)
        response = row["response"]
        events = self.store.list_events(run_id)
        report = self._json_report(row, response, events)
        markdown = self._markdown(report)
        self._record_report_generated(row, report, markdown)
        return {"run_id": run_id, "markdown": markdown, "json_report": report}

    def _record_report_generated(self, row: dict[str, Any], report: dict[str, Any], markdown: str) -> None:
        report_hash = hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.store.put_event(
            new_id("evt-report"),
            "report_generated",
            "report-generator",
            {
                "run_id": report["run_id"],
                "incident_id": report["incident_id"],
                "formats": ["json", "markdown"],
                "json_sha256": report_hash,
                "markdown_bytes": len(markdown.encode("utf-8")),
                "event_count": report["evidence_ledger"]["event_count"],
                "judge_final_verdict": (report.get("judge_audit_event") or {}).get("final_verdict"),
            },
            run_id=row["run_id"],
            incident_id=report["incident_id"],
        )

    def _json_report(self, row: dict[str, Any], response: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
        verdict = response["judge_verdict"]
        return {
            "run_id": response["run_id"],
            "incident_id": response["incident_id"],
            "created_at": row["created_at"],
            "scenario_request": row["request"],
            "red_plan": response["red_plan"],
            "classification": response["classification"],
            "tool_audit": {
                "attack_result": response["attack_result"],
                "defense_results": response["defense_results"],
                "restore_result": response["restore_result"],
            },
            "verification": {
                "impact": response["impact_verification"],
                "recovery": response["recovery_verification"],
            },
            "judge_verdict": verdict,
            "judge_audit_event": verdict["reason"].get("judge_audit_event"),
            "truth_state": response["truth_state"],
            "mission_simulation": response.get("mission_simulation"),
            "llm_plan": response.get("llm_plan"),
            "agent_graph": response.get("agent_graph"),
            "evidence_ledger": {
                "event_count": len(events),
                "event_types": sorted({event["event_type"] for event in events}),
                "records": response.get("evidence_ledger_records")
                or self.store.list_evidence_records(response["incident_id"]),
                "common_fields": [
                    "event_id",
                    "incident_id",
                    "observed_fact",
                    "supporting_evidence",
                    "contradicting_evidence",
                    "selected_action",
                    "policy_result",
                    "tool_result",
                    "verification_result",
                    "mission_impact",
                    "judge_result",
                ],
            },
        }

    def _markdown(self, report: dict[str, Any]) -> str:
        verdict = report["judge_verdict"]
        classification = report["classification"]
        audit = report.get("judge_audit_event") or {}
        labels = ", ".join(verdict["labels"])
        actions = ", ".join(item["tool_name"] for item in report["tool_audit"]["defense_results"])
        return f"""# DAH Agent PoC Run Report

## Scenario

- Run ID: `{report['run_id']}`
- Incident ID: `{report['incident_id']}`
- Mission ID: `{report['scenario_request']['mission_id']}`
- Session ID: `{report['scenario_request']['session_id']}`
- Attack Type: `{report['scenario_request']['attack_type']}`
- Seed: `{report['scenario_request']['seed']}`
- UAV Final Position: `{(report.get('mission_simulation') or {}).get('summary', {}).get('uav_final_position')}`
- UGV Final State: `{(report.get('mission_simulation') or {}).get('summary', {}).get('ugv_final_state')}`
- Safe Stop Second: `{(report.get('mission_simulation') or {}).get('summary', {}).get('safe_stop_second')}`

## Red Plan

- Tool: `{report['red_plan']['tool_name']}`
- Policy Risk: `{report['red_plan']['risk_level']}`
- Target: `{json.dumps(report['red_plan']['target'], sort_keys=True)}`

## Agent Graph

- Framework: `{(report.get('agent_graph') or {}).get('framework', 'N/A')}`
- Purpose: `{(report.get('agent_graph') or {}).get('purpose', 'N/A')}`
- Trace Nodes: `{len((report.get('agent_graph') or {}).get('trace', []))}`

## Blue Classification

- Classification: `{classification['classification']}`
- Attack Score: `{classification['attack_score']}`
- Fault Score: `{classification['fault_score']}`
- Evidence: {', '.join(classification['evidence'])}

## LLM Advisory Plan

- OpenAI Used: `{(report.get('llm_plan') or {}).get('openai_used', False)}`
- Source: `{(report.get('llm_plan') or {}).get('source', 'N/A')}`
- Model: `{(report.get('llm_plan') or {}).get('model', 'N/A')}`
- Applied To Execution: `{(report.get('llm_plan') or {}).get('applied_to_execution', False)}`
- Recommended Actions: `{', '.join((report.get('llm_plan') or {}).get('plan', {}).get('recommended_actions', []))}`

## Tool Audit

- Attack Result: `{report['tool_audit']['attack_result']['policy_result']}`
- Defense Actions: {actions}
- Restore Result: `{report['tool_audit']['restore_result']['policy_result']}`

## Verification

- Impact Verified: `{report['verification']['impact']['success']}`
- Recovery Verified: `{report['verification']['recovery']['success']}`

## Judge Verdict

- Labels: {labels}
- Attack Score: `{verdict['attack_score']}`
- Defense Score: `{verdict['defense_score']}`
- Availability: `{verdict['availability']}`
- Total Score: `{verdict['total_score']}`
- Final Verdict: `{audit.get('final_verdict', 'N/A')}`

## Evidence Ledger

- Event Count: `{report['evidence_ledger']['event_count']}`
- Event Types: {', '.join(report['evidence_ledger']['event_types'])}
"""
