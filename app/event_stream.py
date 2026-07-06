from __future__ import annotations

import json
from typing import Any

from .config import Settings


STREAM_BY_EVENT_TYPE = {
    "baseline_ready": "sim-events",
    "command_delivery_anomaly": "sim-events",
    "impact_verified": "sim-events",
    "red_plan_created": "attack-events",
    "prompt_input_sanitized": "attack-events",
    "langgraph_red_trace": "agent-events",
    "langgraph_blue_trace": "agent-events",
    "langgraph_consistency_checked": "agent-events",
    "llm_plan_requested": "llm-events",
    "llm_plan_completed": "llm-events",
    "llm_plan_failed": "llm-events",
    "full_demo_started": "report-events",
    "full_demo_completed": "report-events",
    "full_demo_failed": "report-events",
    "classification_completed": "defense-events",
    "safe_containment_entered": "defense-events",
    "recovery_verified": "defense-events",
    "tool_executed": "tool-execution-events",
    "tool_denied": "tool-execution-events",
    "tool_duplicate": "tool-execution-events",
    "judge_verdict": "judge-audit-events",
    "report_generated": "report-events",
    "replay_completed": "replay-events",
    "temporal_workflow_started": "workflow-events",
    "temporal_workflow_completed": "workflow-events",
    "temporal_workflow_failed": "workflow-events",
    "batch_experiment_completed": "sim-events",
    "experiment_suite_completed": "sim-events",
}


class RedisStreamPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any | None = None
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.redis_streams_enabled

    def stream_for(self, event_type: str) -> str:
        suffix = STREAM_BY_EVENT_TYPE.get(event_type, "dlq-events")
        return f"{self.settings.redis_stream_prefix}:{suffix}"

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "published": False}
        try:
            client = self._redis()
            event_type = str(event.get("event_type", ""))
            stream = self.stream_for(event_type)
            payload = dict(event)
            if event_type not in STREAM_BY_EVENT_TYPE:
                payload["dlq_reason"] = "unknown_event_type"
            message_id = client.xadd(
                stream,
                {
                    "event_id": str(event.get("event_id", "")),
                    "incident_id": str(event.get("incident_id", "")),
                    "event_type": event_type,
                    "payload_json": json.dumps(payload, sort_keys=True),
                },
                maxlen=self.settings.redis_stream_maxlen,
                approximate=True,
            )
            self.last_error = None
            return {"enabled": True, "published": True, "stream": stream, "message_id": str(message_id)}
        except Exception as exc:  # Redis is optional and must never break the ledger write.
            self.last_error = str(exc)
            return {"enabled": True, "published": False, "error": self.last_error}

    def ping(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "ok": False, "reason": "disabled"}
        try:
            self._redis().ping()
            payload = {"enabled": True, "ok": True, "url": self._safe_url()}
            if self.last_error:
                payload["last_publish_error"] = self.last_error
            return payload
        except Exception as exc:
            self.last_error = str(exc)
            return {"enabled": True, "ok": False, "url": self._safe_url(), "error": self.last_error}

    def _redis(self) -> Any:
        if self._client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError("redis package is not installed") from exc
            self._client = redis.Redis.from_url(
                self.settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
        return self._client

    def _safe_url(self) -> str:
        value = self.settings.redis_url
        if "@" not in value:
            return value
        scheme, rest = value.split("://", 1) if "://" in value else ("redis", value)
        host = rest.split("@", 1)[1]
        return f"{scheme}://***@{host}"
