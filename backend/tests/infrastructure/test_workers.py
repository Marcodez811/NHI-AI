from types import SimpleNamespace

import pytest

from app import broker
from app.config import Settings
from app.worker import taskiq_argv, worker_spec


def test_brokers_are_isolated_and_results_are_retained_for_one_hour():
    assert broker.documents_broker.queue_name == "documents"
    assert broker.tasks_broker.queue_name == "tasks"
    assert broker.tasks_broker.result_backend is broker.result_backend
    assert broker.documents_broker.result_backend is not broker.result_backend
    assert broker.result_backend.prefix_str == "tasks:result"
    assert broker.result_backend.result_ex_time == 3600


def test_task_bindings_use_the_expected_broker_boundaries():
    from app.broker import documents_broker, tasks_broker
    from app.tasks.agents import run
    from app.tasks.documents import delete_document_task, ingest_document_task
    from app.tasks.slides import generate_slides_task

    assert ingest_document_task.task_name == "documents.ingest"
    assert delete_document_task.task_name == "documents.delete"
    assert delete_document_task.broker is documents_broker
    assert run.task_name == "agents.run"
    assert ingest_document_task.broker is documents_broker
    assert run.broker is tasks_broker
    # Slides are an adapter selected by ``agents.run``; no second slide task is
    # registered on either broker.
    assert not hasattr(generate_slides_task, "task_name")


def test_worker_launcher_uses_typed_workload_contract():
    documents = worker_spec("documents")
    tasks = worker_spec("tasks")
    assert (documents.processes, documents.max_async_tasks) == (1, 4)
    assert (tasks.processes, tasks.max_async_tasks) == (2, 2)
    assert taskiq_argv("documents")[-4:] == ["--workers", "1", "--max-async-tasks", "4"]
    assert taskiq_argv("tasks")[2] == "app.broker:tasks_broker"
    assert tasks.modules == ("app.tasks.agents",)
    with pytest.raises(ValueError):
        worker_spec("other")  # type: ignore[arg-type]


def test_worker_launcher_supports_dedicated_scheduler_process():
    scheduler = worker_spec("scheduler")
    assert scheduler.broker == "app.scheduler:scheduler"
    assert scheduler.modules == ("app.tasks.documents",)
    assert taskiq_argv("scheduler") == [
        "taskiq",
        "scheduler",
        "app.scheduler:scheduler",
        "app.tasks.documents",
    ]


def test_new_settings_names_take_precedence_over_deprecated_aliases(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("DOCUMENTS_ROOT", "/new/documents")
    monkeypatch.setenv("SLIDES_DOCUMENTS_ROOT", "/old/documents")
    monkeypatch.setenv("AGENT_JOBS_ROOT", "/new/jobs")
    monkeypatch.setenv("SLIDES_JOBS_ROOT", "/old/jobs")
    settings = Settings(_env_file=None)
    assert str(settings.documents_root) == "/new/documents"
    assert str(settings.agent_jobs_root) == "/new/jobs"


@pytest.mark.asyncio
async def test_lifespan_cleans_up_partially_started_brokers(monkeypatch):
    from app import main

    events: list[str] = []

    class FakeBroker:
        is_worker_process = False

        def __init__(self, name: str, fail: bool = False):
            self.name = name
            self.fail = fail

        async def startup(self):
            events.append(f"start:{self.name}")
            if self.fail:
                raise RuntimeError("startup failed")

        async def shutdown(self):
            events.append(f"stop:{self.name}")

    class FakeRedis:
        async def aclose(self):
            events.append("redis:close")

    monkeypatch.setattr(main, "init_db", lambda: events.append("db:init"))
    monkeypatch.setattr(main, "redis_client", FakeRedis())
    monkeypatch.setattr(main, "documents_broker", FakeBroker("documents"))
    monkeypatch.setattr(main, "tasks_broker", FakeBroker("tasks", fail=True))

    with pytest.raises(RuntimeError, match="startup failed"):
        async with main.lifespan(SimpleNamespace()):
            pass
    assert events == ["db:init", "start:documents", "start:tasks", "redis:close", "stop:tasks", "stop:documents"]
