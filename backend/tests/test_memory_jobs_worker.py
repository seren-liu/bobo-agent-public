from __future__ import annotations

import asyncio

from app.core.config import Settings, resolve_memory_worker_mode
from app.memory import repository
from app.memory.jobs import MemoryJobWorker, process_memory_jobs


def setup_function():
    repository._LOCAL_JOBS.clear()


def test_claim_pending_jobs_marks_rows_running():
    first = repository.enqueue_job("u-worker", "profile_refresh_from_records", {}, None)
    second = repository.enqueue_job("u-worker", "profile_refresh_from_records", {}, None)

    claimed = repository.claim_pending_jobs(limit=1)

    assert len(claimed) == 1
    assert claimed[0]["status"] == "running"
    assert int(claimed[0]["attempt_count"]) == 1

    remaining = repository.list_pending_jobs(limit=10)
    assert {str(item["id"]) for item in remaining} == {str(second["id"])}


def test_process_memory_jobs_claims_and_completes(monkeypatch):
    repository.enqueue_job("u-worker-process", "profile_refresh_from_records", {}, None)
    called = {"count": 0}

    monkeypatch.setattr("app.memory.jobs.refresh_profile_from_records", lambda user_id: called.__setitem__("count", called["count"] + 1))

    processed = process_memory_jobs(limit=5)

    assert len(processed) == 1
    assert processed[0]["status"] == "completed"
    assert processed[0]["attempt_count"] == 1
    assert called["count"] == 1


def test_process_memory_jobs_retryable_failure_reschedules(monkeypatch):
    repository.enqueue_job("u-worker-retry", "profile_refresh_from_records", {}, None)

    def _boom(_user_id):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr("app.memory.jobs.refresh_profile_from_records", _boom)

    processed = process_memory_jobs(limit=5)

    assert len(processed) == 1
    assert processed[0]["status"] == "pending"
    assert repository.count_pending_jobs() == 0


def test_memory_worker_start_stop():
    async def _run():
        worker = MemoryJobWorker(poll_interval_seconds=0.05, batch_size=1, max_backoff_seconds=0.1)
        await worker.start()
        await asyncio.sleep(0.06)
        await worker.stop()

    asyncio.run(_run())


def test_resolve_memory_worker_mode_defaults():
    assert resolve_memory_worker_mode(Settings(env="dev")) == "embedded"
    assert resolve_memory_worker_mode(Settings(env="production")) == "external"
    assert resolve_memory_worker_mode(Settings(env="dev", memory_worker_mode="disabled")) == "disabled"
