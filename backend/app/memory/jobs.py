from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.memory import repository
from app.memory.extraction import persist_extraction_result
from app.memory.profile import refresh_profile_from_records
from app.memory.summaries import refresh_thread_summary

logger = logging.getLogger("bobo.memory.jobs")


def enqueue_memory_job(user_id: str, job_type: str, payload: dict[str, Any], thread_key: str | None = None) -> dict[str, Any]:
    return repository.enqueue_job(user_id=user_id, job_type=job_type, payload=payload, thread_key=thread_key)


def _resolve_thread_key(job: dict[str, Any]) -> str | None:
    payload = job.get("payload") or {}
    thread_key = payload.get("thread_key") or job.get("thread_key")
    return str(thread_key) if thread_key else None


def process_memory_jobs(limit: int = 10) -> list[dict[str, Any]]:
    processed: list[dict[str, Any]] = []
    for job in repository.list_pending_jobs(limit=limit):
        attempt_count = int(job.get("attempt_count") or 0) + 1
        job_id = str(job["id"])
        try:
            repository.mark_job_status(job_id, "running", attempt_count=attempt_count)
            user_id = str(job["user_id"])
            job_type = str(job["job_type"])
            thread_key = _resolve_thread_key(job)

            if job_type == "thread_summary_refresh" and thread_key:
                refresh_thread_summary(user_id, thread_key)
            elif job_type == "memory_extract_from_thread" and thread_key:
                extraction_result = persist_extraction_result(user_id, thread_key)
            elif job_type == "profile_refresh_from_records":
                refresh_profile_from_records(user_id)

            repository.mark_job_status(job_id, "completed", attempt_count=attempt_count)
            processed_job = dict(job)
            processed_job["status"] = "completed"
            processed_job["attempt_count"] = attempt_count
            if job_type == "memory_extract_from_thread" and thread_key:
                processed_job["result"] = extraction_result
            processed.append(processed_job)
        except Exception as exc:
            repository.mark_job_status(job_id, "failed", attempt_count=attempt_count, last_error=str(exc))
            failed_job = dict(job)
            failed_job["status"] = "failed"
            failed_job["attempt_count"] = attempt_count
            failed_job["error"] = str(exc)
            failed_job["error_stage"] = "process_memory_jobs"
            processed.append(failed_job)
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
