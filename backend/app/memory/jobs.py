from __future__ import annotations

import asyncio
import logging
from typing import Any
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from app.memory import repository
from app.memory.extraction import persist_extraction_result
from app.memory.profile import refresh_profile_from_records
from app.memory.summaries import refresh_thread_summary
from app.core.config import get_settings
from app.core.resilience import classify_dependency_error
from app.observability import observe_memory_worker_job, observe_task_execution, set_memory_worker_pending_jobs

logger = logging.getLogger("bobo.memory.jobs")


def enqueue_memory_job(user_id: str, job_type: str, payload: dict[str, Any], thread_key: str | None = None) -> dict[str, Any]:
    return repository.enqueue_job(user_id=user_id, job_type=job_type, payload=payload, thread_key=thread_key)


def _resolve_thread_key(job: dict[str, Any]) -> str | None:
    payload = job.get("payload") or {}
    thread_key = payload.get("thread_key") or job.get("thread_key")
    return str(thread_key) if thread_key else None


def process_memory_jobs(limit: int = 10) -> list[dict[str, Any]]:
    settings = get_settings()
    processed: list[dict[str, Any]] = []
    for job in repository.claim_pending_jobs(limit=limit):
        attempt_count = int(job.get("attempt_count") or 0)
        job_id = str(job["id"])
        job_type = str(job.get("job_type") or "unknown")
        scheduled_at = job.get("scheduled_at")
        lag_seconds = 0.0
        if isinstance(scheduled_at, datetime):
            lag_seconds = max((datetime.now(UTC) - scheduled_at).total_seconds(), 0.0)
        observe_memory_worker_job(job_type=job_type, stage="claimed", lag_seconds=lag_seconds)
        try:
            user_id = str(job["user_id"])
            thread_key = _resolve_thread_key(job)

            if job_type == "thread_summary_refresh" and thread_key:
                refresh_thread_summary(user_id, thread_key)
            elif job_type == "memory_extract_from_thread" and thread_key:
                extraction_result = persist_extraction_result(user_id, thread_key)
            elif job_type == "profile_refresh_from_records":
                refresh_profile_from_records(user_id)

            repository.mark_job_status(job_id, "completed", attempt_count=attempt_count, last_error=None)
            processed_job = dict(job)
            processed_job["status"] = "completed"
            processed_job["attempt_count"] = attempt_count
            if job_type == "memory_extract_from_thread" and thread_key:
                processed_job["result"] = extraction_result
            processed.append(processed_job)
            observe_memory_worker_job(job_type=job_type, stage="completed", lag_seconds=lag_seconds)
            observe_task_execution(task=job_type, outcome="success", source="memory_worker")
        except Exception as exc:
            error = classify_dependency_error(exc, f"memory_job:{job_type}")
            retryable = error.retryable and attempt_count < max(int(settings.memory_worker_max_attempts or 0), 1)
            if retryable:
                delay_seconds = min(
                    float(settings.memory_worker_retry_base_seconds or 10.0) * (2 ** max(attempt_count - 1, 0)),
                    float(settings.memory_worker_retry_max_seconds or settings.memory_worker_max_backoff_seconds or 300.0),
                )
                repository.mark_job_status(
                    job_id,
                    "pending",
                    attempt_count=attempt_count,
                    last_error=str(error),
                    scheduled_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                )
                outcome = "retry_scheduled"
            else:
                repository.mark_job_status(job_id, "failed", attempt_count=attempt_count, last_error=str(error))
                outcome = "failed"
            failed_job = dict(job)
            failed_job["status"] = "pending" if retryable else "failed"
            failed_job["attempt_count"] = attempt_count
            failed_job["error"] = str(error)
            failed_job["error_stage"] = "process_memory_jobs"
            failed_job["error_category"] = error.category
            processed.append(failed_job)
            observe_memory_worker_job(job_type=job_type, stage=outcome, lag_seconds=lag_seconds)
            observe_task_execution(task=job_type, outcome=outcome, source="memory_worker")
    return processed


async def process_memory_jobs_async(limit: int = 10) -> list[dict[str, Any]]:
    return await asyncio.to_thread(process_memory_jobs, limit)


def schedule_memory_jobs(limit: int = 10) -> asyncio.Task[list[dict[str, Any]]]:
    async def _runner() -> list[dict[str, Any]]:
        try:
            return await process_memory_jobs_async(limit)
        except Exception as exc:  # pragma: no cover
            logger.exception("memory background jobs failed: %s", exc)
            return []

    return asyncio.create_task(_runner())


class MemoryJobWorker:
    def __init__(
        self,
        *,
        poll_interval_seconds: float | None = None,
        batch_size: int | None = None,
        max_backoff_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self.poll_interval_seconds = max(float(poll_interval_seconds or settings.memory_worker_poll_interval_seconds or 2.0), 0.1)
        self.batch_size = max(int(batch_size or settings.memory_worker_batch_size or 10), 1)
        self.max_backoff_seconds = max(float(max_backoff_seconds or settings.memory_worker_max_backoff_seconds or 15.0), self.poll_interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="bobo-memory-worker")
        logger.info("memory worker started poll_interval=%.2fs batch_size=%s", self.poll_interval_seconds, self.batch_size)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("memory worker stopped")

    async def _run(self) -> None:
        backoff = self.poll_interval_seconds
        while not self._stop_event.is_set():
            try:
                set_memory_worker_pending_jobs(repository.count_pending_jobs())
                processed = await process_memory_jobs_async(limit=self.batch_size)
                set_memory_worker_pending_jobs(repository.count_pending_jobs())
                if processed:
                    backoff = self.poll_interval_seconds
                    continue
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                logger.exception("memory worker loop failed: %s", exc)
                backoff = min(max(backoff * 2, self.poll_interval_seconds), self.max_backoff_seconds)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    continue
