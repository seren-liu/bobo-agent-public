from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.memory.jobs import MemoryJobWorker
from app.models.db import close_pool, init_pool

logger = logging.getLogger("bobo.memory.worker")


async def _run() -> None:
    settings = get_settings()
    init_pool()
    worker = MemoryJobWorker(
        poll_interval_seconds=settings.memory_worker_poll_interval_seconds,
        batch_size=settings.memory_worker_batch_size,
        max_backoff_seconds=settings.memory_worker_max_backoff_seconds,
    )
    await worker.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise
    finally:
        await worker.stop()
        close_pool()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
