-- PostgreSQL-compatible schema parity for report section 13.1.
-- The PoC runtime uses SQLite for the local Evidence Ledger; Temporal state uses PostgreSQL.

create table if not exists events (
    event_id text primary key,
    run_id text,
    incident_id text,
    event_type text not null,
    source text not null,
    occurred_at timestamptz not null,
    sequence bigint generated always as identity,
    dedup_key text,
    payload_json jsonb not null,
    created_at timestamptz not null default now()
);

create table if not exists incidents (
    incident_id text primary key,
    status text not null,
    classification text,
    attack_score double precision,
    fault_score double precision,
    started_at timestamptz not null,
    closed_at timestamptz
);

create table if not exists evidence_records (
    evidence_id text primary key,
    incident_id text not null,
    event_id text not null,
    evidence_type text not null,
    supporting boolean not null,
    contradicting boolean not null,
    confidence double precision not null,
    payload_json jsonb not null,
    created_at timestamptz not null default now()
);

create table if not exists tool_executions (
    execution_id text primary key,
    event_id text not null,
    incident_id text not null,
    tool_name text not null,
    target_id text not null,
    idempotency_key text not null unique,
    status text not null,
    request_json jsonb not null,
    response_json jsonb not null,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create table if not exists verification_results (
    verification_id text primary key,
    incident_id text not null,
    tool_execution_id text,
    success boolean not null,
    result_json jsonb not null
);

create table if not exists judge_verdicts (
    verdict_id text primary key,
    incident_id text not null,
    mission_id text not null,
    attack_score integer not null,
    defense_score integer not null,
    availability integer not null,
    total_score double precision not null,
    labels_json jsonb not null,
    reason_json jsonb not null,
    created_at timestamptz not null default now()
);

create table if not exists replay_runs (
    replay_id text primary key,
    source_incident_id text not null,
    policy_version text not null,
    tool_version text not null,
    model_id text not null,
    prompt_hash text not null,
    result_match boolean not null,
    payload_json jsonb not null,
    created_at timestamptz not null default now()
);
