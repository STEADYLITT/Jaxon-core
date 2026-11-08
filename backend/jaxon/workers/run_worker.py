"""Background worker: pulls run ids from Redis and executes them.

Run with:  python -m jaxon.workers.run_worker   (or the `jaxon-worker` script)
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket

from jaxon.core.config import Settings, get_settings
from jaxon.core.logging import clear_context, configure_logging, get_logger
from jaxon.runtime import Runtime
from jaxon.services.runs import execute_run

log = get_logger(__name__)


class Worker:
    def __init__(self, rt: Runtime, worker_id: str | None = None) -> None:
        self.rt = rt
        self.worker_id = worker_id or os.environ.get("WORKER_ID") or socket.gethostname()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def process_one(self, block_seconds: float = 5.0) -> bool:
        run_id = await self.rt.queue.dequeue(self.worker_id, block_seconds=block_seconds)
        if run_id is None:
            return False
        clear_context()
        try:
            await execute_run(self.rt, run_id)
        except Exception:
            log.exception("worker.run_failed", run_id=str(run_id))
        finally:
            await self.rt.queue.ack(self.worker_id, run_id)
        return True

    async def serve(self) -> None:
        recovered = await self.rt.queue.recover(self.worker_id)
        log.info("worker.started", worker_id=self.worker_id, recovered=recovered)
        while not self._stop.is_set():
            await self.process_one(block_seconds=2.0)
        log.info("worker.stopped", worker_id=self.worker_id)


async def amain(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    rt = Runtime.build(settings)
    worker = Worker(rt)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.serve()
    finally:
        await rt.aclose()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
