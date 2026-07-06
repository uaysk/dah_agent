from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


_ACTIVITY_TIMEOUT_SECONDS = 300
_ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_attempts=2,
)


@workflow.defn(name="dah_scenario_run")
class ScenarioRunWorkflow:
    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            "run_scenario_activity",
            request,
            start_to_close_timeout=timedelta(seconds=_ACTIVITY_TIMEOUT_SECONDS),
            retry_policy=_ACTIVITY_RETRY,
        )


@workflow.defn(name="dah_batch_experiment")
class BatchExperimentWorkflow:
    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            "run_batch_activity",
            request,
            start_to_close_timeout=timedelta(seconds=_ACTIVITY_TIMEOUT_SECONDS),
            retry_policy=_ACTIVITY_RETRY,
        )


@workflow.defn(name="dah_experiment_suite")
class ExperimentSuiteWorkflow:
    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            "run_suite_activity",
            request,
            start_to_close_timeout=timedelta(seconds=_ACTIVITY_TIMEOUT_SECONDS),
            retry_policy=_ACTIVITY_RETRY,
        )
