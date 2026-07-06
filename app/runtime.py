from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, get_settings
from .event_stream import RedisStreamPublisher
from .langgraph_adapter import LangGraphAgentAdapter
from .services import (
    BatchExperimentRunner,
    BlueAgent,
    ExperimentSuiteRunner,
    Judge,
    OpenAIPlanner,
    RedAgent,
    ReplayRunner,
    ReportGenerator,
    ScenarioRunner,
    Simulator,
    ToolExecutor,
    ToolRegistry,
    Verifier,
)
from .store import Store


@dataclass
class AppRuntime:
    settings: Settings
    store: Store
    registry: ToolRegistry
    simulator: Simulator
    executor: ToolExecutor
    verifier: Verifier
    red: RedAgent
    blue: BlueAgent
    judge: Judge
    scenario_runner: ScenarioRunner
    batch_runner: BatchExperimentRunner
    suite_runner: ExperimentSuiteRunner
    replay_runner: ReplayRunner
    report_generator: ReportGenerator
    openai_planner: OpenAIPlanner
    event_publisher: RedisStreamPublisher
    langgraph_adapter: LangGraphAgentAdapter


def build_runtime(settings: Settings | None = None, *, initialize_store: bool = False) -> AppRuntime:
    resolved = settings or get_settings()
    publisher = RedisStreamPublisher(resolved)
    store = Store(resolved.database_path, event_publisher=publisher)
    registry = ToolRegistry()
    simulator = Simulator(store)
    executor = ToolExecutor(store, simulator, registry)
    verifier = Verifier(store)
    red = RedAgent()
    blue = BlueAgent()
    judge = Judge(store)
    langgraph_adapter = LangGraphAgentAdapter()
    openai_planner = OpenAIPlanner(resolved)
    scenario_runner = ScenarioRunner(store, simulator, executor, red, blue, verifier, judge, langgraph_adapter, openai_planner)
    batch_runner = BatchExperimentRunner(store, scenario_runner)
    suite_runner = ExperimentSuiteRunner(store, batch_runner)
    replay_runner = ReplayRunner(store, scenario_runner)
    report_generator = ReportGenerator(store)
    if initialize_store:
        store.init()
    return AppRuntime(
        settings=resolved,
        store=store,
        registry=registry,
        simulator=simulator,
        executor=executor,
        verifier=verifier,
        red=red,
        blue=blue,
        judge=judge,
        scenario_runner=scenario_runner,
        batch_runner=batch_runner,
        suite_runner=suite_runner,
        replay_runner=replay_runner,
        report_generator=report_generator,
        openai_planner=openai_planner,
        event_publisher=publisher,
        langgraph_adapter=langgraph_adapter,
    )
