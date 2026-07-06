from __future__ import annotations

from datetime import timedelta
from typing import Any

from .config import Settings
from .models import new_id


class TemporalUnavailable(RuntimeError):
    pass


class TemporalGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def health(self) -> dict[str, Any]:
        if not self.settings.temporal_enabled:
            return {
                "enabled": False,
                "ok": False,
                "reason": "disabled",
                "postgres_reuse": self._postgres_reuse_status(),
            }
        try:
            await self._client()
        except Exception as exc:
            return {
                "enabled": True,
                "ok": False,
                "address": self.settings.temporal_address,
                "namespace": self.settings.temporal_namespace,
                "task_queue": self.settings.temporal_task_queue,
                "postgres_reuse": self._postgres_reuse_status(),
                "error": str(exc),
            }
        return {
            "enabled": True,
            "ok": True,
            "address": self.settings.temporal_address,
            "namespace": self.settings.temporal_namespace,
            "task_queue": self.settings.temporal_task_queue,
            "postgres_reuse": self._postgres_reuse_status(),
        }

    def _postgres_reuse_status(self) -> dict[str, Any]:
        return {
            "host": self.settings.temporal_db_host,
            "port": self.settings.temporal_db_port,
            "user_configured": bool(self.settings.temporal_db_user.strip()),
            "password_configured": bool(self.settings.temporal_db_password.strip()),
            "database": self.settings.temporal_db_name,
            "visibility_database": self.settings.temporal_visibility_db_name,
        }

    async def execute_scenario(self, request: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        from .temporal_workflows import ScenarioRunWorkflow

        workflow_id = f"dah-scenario-{new_id('wf')}"
        result = await self._execute(ScenarioRunWorkflow.run, request, workflow_id)
        return self._envelope(workflow_id, result)

    async def execute_batch(self, request: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        from .temporal_workflows import BatchExperimentWorkflow

        workflow_id = f"dah-batch-{new_id('wf')}"
        result = await self._execute(BatchExperimentWorkflow.run, request, workflow_id)
        return self._envelope(workflow_id, result)

    async def execute_suite(self, request: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        from .temporal_workflows import ExperimentSuiteWorkflow

        workflow_id = f"dah-suite-{new_id('wf')}"
        result = await self._execute(ExperimentSuiteWorkflow.run, request, workflow_id)
        return self._envelope(workflow_id, result)

    async def _execute(self, workflow: Any, request: dict[str, Any], workflow_id: str) -> dict[str, Any]:
        client = await self._client()
        return await client.execute_workflow(
            workflow,
            request,
            id=workflow_id,
            task_queue=self.settings.temporal_task_queue,
            execution_timeout=timedelta(seconds=self.settings.temporal_workflow_timeout_seconds),
        )

    def _ensure_enabled(self) -> None:
        if not self.settings.temporal_enabled:
            raise TemporalUnavailable("Temporal is disabled. Set TEMPORAL_ENABLED=true and start the temporal profile.")

    async def _client(self) -> Any:
        self._ensure_enabled()
        try:
            from temporalio.client import Client
        except ImportError as exc:
            raise TemporalUnavailable("temporalio package is not installed") from exc
        try:
            return await Client.connect(self.settings.temporal_address, namespace=self.settings.temporal_namespace)
        except Exception as exc:
            raise TemporalUnavailable(f"Temporal connection failed: {exc}") from exc

    def _envelope(self, workflow_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "workflow_id": workflow_id,
            "namespace": self.settings.temporal_namespace,
            "task_queue": self.settings.temporal_task_queue,
            "result": result,
        }
