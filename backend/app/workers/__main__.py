"""``python -m app.workers``: run the background worker until SIGTERM/SIGINT."""

import asyncio
import logging
import signal
import socket

from app.adapters.clock import SystemClock
from app.adapters.document_html import HtmlDocumentRenderer
from app.adapters.local_storage import LocalDocumentStorage
from app.adapters.provider_http import HttpProviderClient
from app.adapters.sqlalchemy.uow import make_engine, session_factory
from app.config import Settings
from app.workers.worker import JobWorker


async def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = make_engine(settings.database_url)
    provider = HttpProviderClient(settings.provider_base_url, settings.provider_token,
                                  settings.provider_timeout_seconds)
    worker = JobWorker(
        session_factory(engine), provider, LocalDocumentStorage(settings.document_storage_dir),
        HtmlDocumentRenderer(), SystemClock(), settings.reconcile_policy(),
        lease_seconds=settings.worker_job_lease_seconds, batch_size=settings.worker_batch_size,
        document_sweep_seconds=settings.document_sweep_interval_seconds,
        name=f"worker@{socket.gethostname()}",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)  # finish the current job, then exit
    try:
        await worker.run_forever(stop, settings.worker_poll_interval_seconds)
    finally:
        await provider.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
