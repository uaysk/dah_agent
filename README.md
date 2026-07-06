# DAH 에이전트 PoC

이 저장소는 DAH 2026 Red/Blue 에이전트 설계를 검증하기 위한 Docker 우선 PoC입니다. 새로 생성하는 런타임 구성요소는 컨테이너로 실행하며, 설정에 따라 기존 홈랩 Kubernetes의 PostgreSQL/Redis 서비스를 재사용할 수 있습니다.

이 앱은 다음 폐루프 구조를 구현합니다.

```text
외부 API / 대시보드
  -> FastAPI 게이트웨이
  -> 로컬 ScenarioRunner 또는 선택적 Temporal Workflow
  -> LangGraph Red/Blue 추론 추적 어댑터
  -> Red Agent -> YAML Tool Registry -> Policy Gateway -> Single Tool Executor
  -> Mock UAV/UGV/GCS/Satellite Simulator + Truth State
  -> 검증 -> Blue Agent -> 방어/복구 도구 -> Judge
  -> SQLite Evidence Ledger -> 선택적 Redis Streams 미러링 -> Reports/Metrics/Grafana
```

이 구현은 의도적으로 임베딩 모델이나 reranker 모델을 사용하지 않습니다. OpenAI/LiteLLM은 `POST /scenarios/run` 실행 중 자문용 typed-plan 경로와 `POST /llm/plan` 직접 호출 경로에서만 사용됩니다. LLM 출력은 감사 로그에 기록되며 도구를 직접 실행하지 않습니다. OpenAI 키가 없어도 E2E 시나리오는 결정적 fallback 자문 계획을 기록하면서 동작합니다.

## 실행

```bash
cp .env.example .env
# OPENAI_BASE_URL 기본값은 https://litellm.uaysk.com 입니다.
# /scenarios/run 및 /llm/plan에서 LiteLLM/OpenAI를 호출하려면 .env에 OPENAI_API_KEY를 설정합니다.
docker compose up --build -d
```

Temporal workflow 모드는 별도 데이터베이스 컨테이너를 만들지 않고 기존 홈랩 K8s PostgreSQL 서비스를 재사용합니다. `.env`의 `TEMPORAL_DB_*` 값을 채우고 `TEMPORAL_ENABLED=true`로 설정한 뒤 실행합니다. Compose profile은 Temporal 파일 기반 동적 설정을 위해 `temporal/dynamicconfig/development-sql.yaml`을 마운트합니다.

```bash
docker compose --profile temporal up --build -d
```

Temporal 엔드포인트:

- Temporal gRPC: `172.30.1.1:17233`
- Temporal UI: `http://172.30.1.1:18233`
- API 상태 확인: `http://172.30.1.1:18080/temporal/health`
- Temporal을 통한 시나리오 실행: `POST /temporal/scenarios/run`
- Temporal을 통한 E0-E5 suite 실행: `POST /temporal/experiments/run-suite`

API:

- Swagger: `http://172.30.1.1:18080/docs`
- Health: `http://172.30.1.1:18080/health`

## 외부 API 테스트

```bash
curl http://172.30.1.1:18080/health

curl -sS -X POST http://172.30.1.1:18080/scenarios/run \
  -H 'content-type: application/json' \
  -d '{
    "mission_id": "mission-alpha",
    "session_id": "session-17",
    "attack_type": "selective_message_drop",
    "duration_seconds": 30,
    "seed": 26063001
  }'

curl -sS -X POST http://172.30.1.1:18080/demo/run-full \
  -H 'content-type: application/json' \
  -d '{}'
```

시나리오 응답에는 Red 계획, 공격 도구 결과, Blue 분류, LLM 자문 typed plan, 방어 도구 결과, 복구 검증, Judge 판정, Truth State, LangGraph/OpenAI 자문 그래프 추적, Evidence Ledger 기록, 이벤트 ledger 항목이 포함됩니다.

## 보고서 첨부용 산출물

경량 UAV/UGV 임무 시뮬레이션:

```bash
curl -sS -X POST http://172.30.1.1:18080/sim/mission \
  -H 'content-type: application/json' \
  -d '{"duration_seconds":60,"drop_start_second":10,"drop_end_second":35}'
```

시뮬레이션 정책:

- UAV는 고정 waypoint를 향해 이동하며 매 tick마다 좌표 보고를 생성합니다.
- UGV는 age가 5초 이하인 좌표만 따라갑니다.
- 유효 좌표를 15초 동안 받지 못하면 UGV는 `SAFE_STOP_CAUSED_BY_COORD_STALE` 상태로 진입합니다.
- Gateway trace는 Return Link 좌표 보고의 전달/드롭 여부를 기록합니다.
- GCS display trace는 replay로 인해 발생한 표시값과 실제값의 불일치를 기록합니다.

반복 seed 기반 batch experiment:

```bash
curl -sS -X POST http://172.30.1.1:18080/experiments/run-batch \
  -H 'content-type: application/json' \
  -d '{"runs":30,"seed_start":26063001,"duration_seconds":30}'
```

DAH E0-E5 experiment suite를 smoke scale 또는 report scale로 실행:

```bash
curl -sS -X POST http://172.30.1.1:18080/experiments/run-suite \
  -H 'content-type: application/json' \
  -d '{"runs_per_group":3,"duration_seconds":30}'
```

Experiment group:

- E0: 정상 baseline
- E1: 저율 random packet loss fault profile
- E2/E3/E4: 내장 Red/Blue loop를 사용하는 P1 좌표 보고 suppression
- E5: 제한된 P2 recovery/resync interference

Suite 응답은 attack group 탐지율, false negative rate, fault group false positive rate, 탐지/복구 지연시간, 전체 run 수, group별 run ID를 보고합니다.

저장된 run을 replay하고 policy/Judge 결정성을 비교:

```bash
curl -sS -X POST http://172.30.1.1:18080/replay/{run_id}
```

보고서 산출물 export:

```bash
curl -sS http://172.30.1.1:18080/reports/{run_id}.json
curl -sS http://172.30.1.1:18080/reports/{run_id}.md
```

JSON/Markdown 보고서에는 scenario request, Red plan, Blue evidence, tool audit, verification results, Judge verdict, Appendix A.4 형식의 Judge audit event, 공통 보고서 필드를 포함한 Evidence Ledger 기록이 들어갑니다.

보고서 첨부용 coverage map:

```bash
curl -sS http://172.30.1.1:18080/reports/coverage.json
```

## Redis Streams

Redis Streams는 선택 사항이며, 활성화 시 기존 홈랩 Redis 엔드포인트를 사용합니다. 이 compose 파일은 Redis 컨테이너를 생성하지 않습니다.

```bash
REDIS_STREAMS_ENABLED=true
REDIS_URL=redis://172.30.1.51:6379/0
curl http://172.30.1.1:18080/streams/status
```

이벤트는 `dah:sim-events`, `dah:attack-events`, `dah:agent-events`, `dah:llm-events`, `dah:defense-events`, `dah:tool-execution-events`, `dah:judge-audit-events`, `dah:workflow-events`, `dah:report-events`, `dah:replay-events`, `dah:dlq-events`로 매핑됩니다. Redis publish 실패는 SQLite Evidence Ledger 기록을 막지 않습니다.

## 안전 경계

- 실제 UAV, UGV, RF, 위성, shell, SQL, 외부 공격 대상에 접근하지 않습니다.
- 상태 변경은 `POST /tools/execute`를 통해서만 수행됩니다.
- Tool contract는 `app/policy/tool_registry.yaml`에서 로드되며 `GET /tools/registry`로 노출됩니다.
- 알 수 없는 도구, 안전하지 않은 target, A4 형식 action은 Policy Gateway에서 거부됩니다.
- LLM 출력은 자문용 typed-plan 데이터일 뿐이며, `llm_plan_*` 이벤트에 기록되고 도구를 직접 실행하지 않습니다.

## 실시간 에이전트 대시보드

shadcn/ui 기반 대시보드는 별도 Docker 컨테이너로 제공되며, FastAPI 앱의 SSE live event stream을 사용합니다. UI는 기본적으로 dark operations theme을 사용하고, 들어오는 이벤트가 참조하는 graph node/edge를 pulse 효과로 강조합니다.

- URL: `http://172.30.1.1:18081`
- State API: `http://172.30.1.1:18080/dashboard/state`
- Live SSE API: `http://172.30.1.1:18080/dashboard/events`
- FastAPI, Temporal, LangGraph, Red/Blue agents, Tool Registry, Policy Gateway, Single Tool Executor, UAV/UGV simulator, Truth State, verifier, recovery, Judge, SQLite Evidence Ledger, Redis Streams, OpenAI/LiteLLM, reports, metrics, Grafana를 시각화합니다.
- `/scenarios/run`, `/temporal/scenarios/run`, `/experiments/run-suite`, `/llm/plan` 외부 API 호출은 SSE stream을 통해 반영됩니다. 각 ledger event에는 대시보드에서 pulse 처리할 graph node 및 edge ID가 포함됩니다. OpenAI/LiteLLM node는 `llm_plan_requested`, `llm_plan_completed`, `llm_plan_failed`에서 pulse 처리됩니다.
- Demo Controls는 Local P1, Temporal P1, E0-E5 Suite, direct LLM Advisory, Full Demo + Report를 실행합니다. Full Demo는 `/demo/run-full`을 호출하여 Local/Temporal/Suite/LLM/Report 경로를 모두 실행한 뒤 생성된 JSON report를 브라우저에서 다운로드합니다.

## Grafana 대시보드

Grafana는 별도 Docker 컨테이너로 provision됩니다.

- URL: `http://172.30.1.1:13000`
- User: `admin`
- Password: `.env`의 `GRAFANA_ADMIN_PASSWORD` 값
- Dashboard: `DAH Agent PoC - Red/Blue Mission Metrics`

Grafana는 앱의 경량 Prometheus 호환 엔드포인트인 `http://dah-agent-poc:8080/prometheus`를 built-in Prometheus datasource로 사용합니다. 별도 Prometheus 컨테이너는 필요하지 않습니다.

유용한 직접 metric 확인:

```bash
curl 'http://172.30.1.1:18080/prometheus/api/v1/query?query=dah_total_runs'
curl 'http://172.30.1.1:18080/prometheus/api/v1/query?query=dah_average_total_score'
```

## 데모 스크린샷

아래 2560x1600 스크린샷은 `POST /demo/run-full` 실행 후 Browserless로 캡처했습니다. 에이전트 실행, Temporal durable workflow evidence, Grafana metrics로 이어지는 주요 시연 경로를 보여줍니다.

### 1. 에이전트 대시보드 - Full Demo 실행

에이전트 대시보드는 operator-facing view입니다. `Temporal online`, Redis 및 LLM 상태, `Full Demo + Report` control, live event stream 활동, FastAPI, Temporal, LangGraph, Red/Blue agents, policy, simulator, evidence ledger, reports, metrics, Grafana를 포함하는 연결된 execution graph를 보여줍니다.

![Full Demo 실행을 표시하는 에이전트 대시보드](docs/screenshots/01-agent-dashboard-full-demo.png)

### 2. Temporal 대시보드 - 완료된 Workflow 목록

Temporal 대시보드는 선택적 durable path가 활성 상태임을 확인합니다. 완료된 `dah_scenario_run` workflow execution이 `default` namespace에 표시되며, `/temporal/scenarios/run` 및 Full Demo temporal step이 Temporal server와 worker에 도달했음을 증명합니다.

![Temporal 대시보드 workflow 목록](docs/screenshots/02-temporal-workflows.png)

### 3. Temporal 대시보드 - Workflow 상세

Workflow 상세 화면은 선택된 `dah_scenario_run` execution, task queue, duration, input payload, result payload를 보여줍니다. 이 화면은 Temporal-backed scenario step의 durable audit trail입니다.

![Temporal workflow 상세 화면](docs/screenshots/03-temporal-workflow-detail.png)

### 4. Grafana 대시보드 - Mission Metrics

Grafana는 DAH API가 제공하는 Prometheus 호환 metric을 시각화합니다. 대시보드는 total runs, detection rate, recovery success rate, total score, availability, safe-stop rate, coordinate gap, 최근 Full Demo 및 scenario run으로 채워진 trend panel을 포함합니다.

![Grafana mission metrics 대시보드](docs/screenshots/04-grafana-metrics-dashboard.png)

## 로컬 테스트

```bash
pytest
```

## 구현 범위

이 저장소는 보고서의 PoC/MVP 범위를 구현하고, 기존 홈랩 서비스를 재사용하는 선택적 durable workflow/event-stream 경로를 추가합니다. 현재 구현 범위는 다음과 같습니다.

- P1 좌표 보고 suppression, P2 제한적 recovery interference, P3 display replay-effect simulation.
- `/experiments/run-suite`를 통한 E0-E5 smoke/report-scale experiment suite.
- 명시적 `command_delivery_anomaly`, `prompt_input_sanitized`, `safe_containment_entered` evidence event.
- 정상 baseline 및 저율 random packet loss에 대한 fault-vs-attack classification.
- Single Tool Executor, YAML Tool Registry allowlist, Policy Gateway decision, idempotency, verification, replay, Judge scoring, JSON/Markdown reports, Grafana metrics.
- LangGraph Red/Blue reasoning trace adapter와 OpenAI/LiteLLM advisory trace node. Durable orchestration은 Temporal/local runner가 담당합니다.
- 기존 K8s PostgreSQL을 재사용하는 Temporal server/UI/worker compose profile.
- 기존 홈랩 Redis 서비스를 사용하는 선택적 Redis Streams publisher.
- Evidence Ledger 공통 필드, incident/evidence/replay table, `/truth/events`, report coverage API, 모듈화된 `red_agent/`, `blue_agent/`, `judge/`, `policy/` package.
