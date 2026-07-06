from __future__ import annotations

from typing import Any

from temporalio import activity

from .models import BatchExperimentRequest, ExperimentSuiteRequest, ScenarioRunRequest
from .runtime import AppRuntime, build_runtime

_RUNTIME: AppRuntime | None = None


def _runtime() -> AppRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = build_runtime(initialize_store=True)
    return _RUNTIME


@activity.defn(name="run_scenario_activity")
def run_scenario_activity(request: dict[str, Any]) -> dict[str, Any]:
    return _runtime().scenario_runner.run(ScenarioRunRequest.model_validate(request))


@activity.defn(name="run_batch_activity")
def run_batch_activity(request: dict[str, Any]) -> dict[str, Any]:
    return _runtime().batch_runner.run_batch(BatchExperimentRequest.model_validate(request))


@activity.defn(name="run_suite_activity")
def run_suite_activity(request: dict[str, Any]) -> dict[str, Any]:
    return _runtime().suite_runner.run_suite(ExperimentSuiteRequest.model_validate(request))
