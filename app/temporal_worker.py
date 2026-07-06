from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from .config import get_settings
from .temporal_activities import run_batch_activity, run_scenario_activity, run_suite_activity
from .temporal_workflows import BatchExperimentWorkflow, ExperimentSuiteWorkflow, ScenarioRunWorkflow


async def _connect_with_retry() -> Client:
    settings = get_settings()
    last_error: Exception | None = None
    for attempt in range(1, 31):
        try:
            return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
        except Exception as exc:
            last_error = exc
            logging.warning("Temporal connect attempt %s failed: %s", attempt, exc)
            await asyncio.sleep(min(attempt, 5))
    raise RuntimeError(f"Temporal worker could not connect: {last_error}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    client = await _connect_with_retry()
    with ThreadPoolExecutor(max_workers=4) as activity_executor:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[ScenarioRunWorkflow, BatchExperimentWorkflow, ExperimentSuiteWorkflow],
            activities=[run_scenario_activity, run_batch_activity, run_suite_activity],
            activity_executor=activity_executor,
        )
        logging.info("Temporal worker started task_queue=%s namespace=%s", settings.temporal_task_queue, settings.temporal_namespace)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
