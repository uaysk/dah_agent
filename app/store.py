from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol

from .models import JudgeVerdict, TruthSession, now_iso


class EventPublisher(Protocol):
    def publish(self, event: dict[str, Any]) -> dict[str, Any]: ...


class Store:
    def __init__(self, database_path: str, event_publisher: EventPublisher | None = None):
        self.database_path = database_path
        self.event_publisher = event_publisher
        self._lock = threading.RLock()

    def init(self) -> None:
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists events (
                    event_id text primary key,
                    run_id text,
                    incident_id text,
                    event_type text not null,
                    source text not null,
                    occurred_at text not null,
                    payload_json text not null
                );

                create table if not exists runs (
                    run_id text primary key,
                    incident_id text not null,
                    status text not null,
                    request_json text not null,
                    response_json text not null,
                    created_at text not null
                );

                create table if not exists incidents (
                    incident_id text primary key,
                    status text not null,
                    classification text,
                    attack_score real,
                    fault_score real,
                    started_at text not null,
                    closed_at text
                );

                create table if not exists evidence_records (
                    evidence_id text primary key,
                    incident_id text not null,
                    event_id text not null,
                    evidence_type text not null,
                    supporting integer not null,
                    contradicting integer not null,
                    confidence real not null,
                    payload_json text not null,
                    created_at text not null
                );

                create table if not exists truth_sessions (
                    session_id text primary key,
                    mission_id text not null,
                    payload_json text not null,
                    updated_at text not null
                );

                create table if not exists tool_executions (
                    execution_id text primary key,
                    event_id text not null,
                    incident_id text not null,
                    tool_name text not null,
                    target_id text not null,
                    idempotency_key text not null unique,
                    status text not null,
                    request_json text not null,
                    response_json text not null,
                    created_at text not null
                );

                create table if not exists verification_results (
                    verification_id text primary key,
                    incident_id text not null,
                    success integer not null,
                    result_json text not null,
                    created_at text not null
                );

                create table if not exists judge_verdicts (
                    verdict_id text primary key,
                    incident_id text not null,
                    mission_id text not null,
                    attack_score integer not null,
                    defense_score integer not null,
                    availability integer not null,
                    total_score real not null,
                    labels_json text not null,
                    reason_json text not null,
                    created_at text not null
                );

                create table if not exists replay_runs (
                    replay_id text primary key,
                    source_incident_id text not null,
                    policy_version text not null,
                    tool_version text not null,
                    model_id text not null,
                    prompt_hash text not null,
                    result_match integer not null,
                    payload_json text not null,
                    created_at text not null
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def ping(self) -> bool:
        with self._connect() as conn:
            conn.execute("select 1").fetchone()
        return True

    def put_event(
        self,
        event_id: str,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        run_id: str | None = None,
        incident_id: str | None = None,
    ) -> None:
        occurred_at = now_iso()
        event = {
            "event_id": event_id,
            "run_id": run_id,
            "incident_id": incident_id,
            "event_type": event_type,
            "source": source,
            "occurred_at": occurred_at,
            "payload": payload,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert or replace into events
                (event_id, run_id, incident_id, event_type, source, occurred_at, payload_json)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    run_id,
                    incident_id,
                    event_type,
                    source,
                    occurred_at,
                    json.dumps(payload, sort_keys=True),
                ),
            )
        if self.event_publisher:
            self.event_publisher.publish(event)

    def _event_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        event = {
            "event_id": row["event_id"],
            "run_id": row["run_id"],
            "incident_id": row["incident_id"],
            "event_type": row["event_type"],
            "source": row["source"],
            "occurred_at": row["occurred_at"],
            "payload": json.loads(row["payload_json"]),
        }
        if "stream_id" in row.keys():
            event["stream_id"] = int(row["stream_id"])
        return event

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select rowid as stream_id, * from events where run_id = ? order by occurred_at asc",
                (run_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select rowid as stream_id, * from events order by rowid desc limit ?",
                (limit,),
            ).fetchall()
        return [self._event_from_row(row) for row in reversed(rows)]

    def list_events_after_rowid(self, stream_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select rowid as stream_id, * from events where rowid > ? order by rowid asc limit ?",
                (stream_id, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def latest_event_rowid(self) -> int:
        with self._connect() as conn:
            row = conn.execute("select coalesce(max(rowid), 0) as stream_id from events").fetchone()
        return int(row["stream_id"] if row else 0)


    def put_incident(
        self,
        incident_id: str,
        status: str,
        classification: str | None = None,
        attack_score: float | None = None,
        fault_score: float | None = None,
        closed_at: str | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "select started_at from incidents where incident_id = ?",
                (incident_id,),
            ).fetchone()
            started_at = existing["started_at"] if existing else now_iso()
            conn.execute(
                """
                insert or replace into incidents
                (incident_id, status, classification, attack_score, fault_score, started_at, closed_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (incident_id, status, classification, attack_score, fault_score, started_at, closed_at),
            )

    def put_evidence_record(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert or replace into evidence_records
                (evidence_id, incident_id, event_id, evidence_type, supporting, contradicting, confidence, payload_json, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["evidence_id"],
                    record["incident_id"],
                    record["event_id"],
                    record["evidence_type"],
                    1 if record.get("supporting") else 0,
                    1 if record.get("contradicting") else 0,
                    float(record.get("confidence", 0.0)),
                    json.dumps(record, sort_keys=True),
                    now_iso(),
                ),
            )

    def list_evidence_records(self, incident_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select payload_json from evidence_records where incident_id = ? order by created_at asc",
                (incident_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def put_truth(self, truth: TruthSession) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert or replace into truth_sessions
                (session_id, mission_id, payload_json, updated_at)
                values (?, ?, ?, ?)
                """,
                (
                    truth.session_id,
                    truth.mission_id,
                    json.dumps(truth.model_dump(), sort_keys=True),
                    truth.updated_at,
                ),
            )

    def get_truth(self, session_id: str) -> TruthSession | None:
        with self._connect() as conn:
            row = conn.execute(
                "select payload_json from truth_sessions where session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return TruthSession.model_validate(json.loads(row["payload_json"]))

    def list_truth_by_mission(self, mission_id: str) -> list[TruthSession]:
        with self._connect() as conn:
            rows = conn.execute(
                "select payload_json from truth_sessions where mission_id = ?",
                (mission_id,),
            ).fetchall()
        return [TruthSession.model_validate(json.loads(row["payload_json"])) for row in rows]

    def get_tool_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "select * from tool_executions where idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if not row:
            return None
        return {
            "execution_id": row["execution_id"],
            "event_id": row["event_id"],
            "incident_id": row["incident_id"],
            "tool_name": row["tool_name"],
            "target_id": row["target_id"],
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "response": json.loads(row["response_json"]),
            "created_at": row["created_at"],
        }

    def put_tool_execution(
        self,
        execution_id: str,
        event_id: str,
        incident_id: str,
        tool_name: str,
        target_id: str,
        idempotency_key: str,
        status: str,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert into tool_executions
                (execution_id, event_id, incident_id, tool_name, target_id, idempotency_key,
                 status, request_json, response_json, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    event_id,
                    incident_id,
                    tool_name,
                    target_id,
                    idempotency_key,
                    status,
                    json.dumps(request, sort_keys=True),
                    json.dumps(response, sort_keys=True),
                    now_iso(),
                ),
            )

    def put_verification(self, verification_id: str, incident_id: str, success: bool, result: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert or replace into verification_results
                (verification_id, incident_id, success, result_json, created_at)
                values (?, ?, ?, ?, ?)
                """,
                (
                    verification_id,
                    incident_id,
                    1 if success else 0,
                    json.dumps(result, sort_keys=True),
                    now_iso(),
                ),
            )


    def put_replay_run(self, replay_id: str, source_incident_id: str, result_match: bool, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert or replace into replay_runs
                (replay_id, source_incident_id, policy_version, tool_version, model_id, prompt_hash, result_match, payload_json, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    replay_id,
                    source_incident_id,
                    "policy-v0.1",
                    "tool-registry-v0.1",
                    "rule-fallback-or-openai",
                    "stored-typed-plan",
                    1 if result_match else 0,
                    json.dumps(payload, sort_keys=True),
                    now_iso(),
                ),
            )

    def put_judge_verdict(self, verdict: JudgeVerdict) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert or replace into judge_verdicts
                (verdict_id, incident_id, mission_id, attack_score, defense_score, availability,
                 total_score, labels_json, reason_json, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verdict.verdict_id,
                    verdict.incident_id,
                    verdict.mission_id,
                    verdict.attack_score,
                    verdict.defense_score,
                    verdict.availability,
                    verdict.total_score,
                    json.dumps(verdict.labels, sort_keys=True),
                    json.dumps(verdict.reason, sort_keys=True),
                    now_iso(),
                ),
            )

    def put_run(self, run_id: str, incident_id: str, status: str, request: dict[str, Any], response: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert or replace into runs
                (run_id, incident_id, status, request_json, response_json, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    incident_id,
                    status,
                    json.dumps(request, sort_keys=True),
                    json.dumps(response, sort_keys=True),
                    now_iso(),
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from runs where run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return {
            "run_id": row["run_id"],
            "incident_id": row["incident_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "response": json.loads(row["response_json"]),
            "created_at": row["created_at"],
        }

    def list_runs(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select * from runs order by created_at asc limit ?",
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "incident_id": row["incident_id"],
                "status": row["status"],
                "request": json.loads(row["request_json"]),
                "response": json.loads(row["response_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_latest_run(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from runs order by created_at desc limit 1").fetchone()
        if not row:
            return None
        return {
            "run_id": row["run_id"],
            "incident_id": row["incident_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "response": json.loads(row["response_json"]),
            "created_at": row["created_at"],
        }

    def run_metric_signature(self, limit: int = 500) -> tuple[int, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select count(*) as row_count, max(created_at) as newest_created_at
                from (select created_at from runs order by created_at desc limit ?)
                """,
                (limit,),
            ).fetchone()
        return int(row["row_count"]), row["newest_created_at"]

    def list_run_metric_rows(self, limit: int = 500) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    select
                        created_at,
                        json_extract(request_json, '$.attack_type') as attack_type,
                        json_extract(request_json, '$.duration_seconds') as duration_seconds,
                        json_extract(response_json, '$.classification.classification') as classification,
                        json_extract(response_json, '$.recovery_verification.success') as recovered,
                        json_extract(response_json, '$.impact_verification.result.first_anomaly_second') as first_anomaly_second,
                        json_extract(response_json, '$.impact_verification.result.detection_threshold_second') as detection_threshold_second,
                        json_extract(response_json, '$.mission_simulation.summary.safe_stop_triggered') as safe_stop_triggered,
                        json_extract(response_json, '$.mission_simulation.summary.max_consecutive_coordinate_gap_seconds') as coordinate_gap_seconds,
                        json_extract(response_json, '$.mission_simulation.summary.safe_stop_second') as safe_stop_second,
                        json_extract(response_json, '$.judge_verdict.total_score') as total_score,
                        json_extract(response_json, '$.judge_verdict.availability') as availability
                    from (
                        select created_at, request_json, response_json
                        from runs
                        order by created_at desc
                        limit ?
                    ) as recent_runs
                    order by created_at asc
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.OperationalError:
            return self.list_runs(limit)

        return [
            {
                "created_at": row["created_at"],
                "scenario_is_attack": row["attack_type"] not in {"random_packet_loss", "normal_baseline"},
                "detected": row["classification"] in {"ATTACK_SUSPECTED", "ATTACK_CONFIRMED"},
                "recovered": bool(row["recovered"]),
                "safe_stop_triggered": bool(row["safe_stop_triggered"]),
                "first_anomaly_second": row["first_anomaly_second"],
                "detection_threshold_second": row["detection_threshold_second"],
                "safe_stop_second": row["safe_stop_second"],
                "duration_seconds": float(row["duration_seconds"] or 0),
                "coordinate_gap_seconds": float(row["coordinate_gap_seconds"] or 0),
                "total_score": float(row["total_score"] or 0),
                "availability": float(row["availability"] or 0),
            }
            for row in rows
        ]
