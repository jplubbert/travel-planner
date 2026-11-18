"""Background worker: runs durable jobs from the ``jobs`` table (ADR 0005).

- ``sync_booking``: booking reconciliation (ADR 0003). Never gives up.
- ``generate_document``: document generation (US-09). Gives up after ``max_attempts``
  and marks the document failed, so it can be retried explicitly.

Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` and a lease: several workers can run, a
job is processed by one of them at a time, and a job whose worker died is picked up again
when its lease expires. Each iteration also schedules interrupted booking submissions and,
periodically, reconciles stored document files with their records.
"""

import asyncio
import contextlib
import logging
import time
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.sqlalchemy.uow import SqlUnitOfWork
from app.domain.document_service import (
    GENERATE_JOB,
    generate_document,
    give_up_generation,
    sweep_documents,
)
from app.domain.jobs import Job, JobStatus
from app.domain.reservations import (
    SYNC_JOB,
    ReconcilePolicy,
    sweep_stale_submitted,
    sync_booking,
)
from app.observability import request_id_var
from app.ports.clock import Clock
from app.ports.documents import DocumentRenderer, DocumentStorage
from app.ports.provider import ProviderClient

logger = logging.getLogger("app.worker")


class JobWorker:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], provider: ProviderClient,
        storage: DocumentStorage, renderer: DocumentRenderer, clock: Clock,
        policy: ReconcilePolicy, *, lease_seconds: float = 120.0, batch_size: int = 10,
        document_sweep_seconds: float = 300.0, name: str = "worker",
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.storage = storage
        self.renderer = renderer
        self.clock = clock
        self.policy = policy
        self.lease = timedelta(seconds=lease_seconds)
        self.batch_size = batch_size
        self.document_sweep_seconds = document_sweep_seconds
        self.name = name
        self._last_document_sweep: float | None = None

    async def sweep(self) -> int:
        """Finds ``submitted`` bookings whose sender stopped and schedules them."""
        async with SqlUnitOfWork(self.sessions) as uow:
            count = await sweep_stale_submitted(uow, self.clock, self.policy)
        if count:
            logger.warning("%s: %d interrupted booking submission(s) scheduled for "
                           "reconciliation", self.name, count)
        return count

    async def sweep_documents(self) -> None:
        async with SqlUnitOfWork(self.sessions) as uow:
            report = await sweep_documents(uow, self.storage, self.clock)
        if report.orphans_removed or report.temp_files_removed or report.missing_files:
            logger.warning("%s: storage sweep removed %d orphan file(s) and %d temp file(s); "
                           "%d document(s) with a missing file sent back to generation",
                           self.name, len(report.orphans_removed),
                           report.temp_files_removed, len(report.missing_files))
        self._last_document_sweep = time.monotonic()

    async def claim(self) -> list[Job]:
        now = self.clock.now()
        async with SqlUnitOfWork(self.sessions) as uow:
            jobs = await uow.jobs.claim(now, now + self.lease, self.batch_size)
            await uow.commit()
        return jobs

    async def run_once(self) -> int:
        """Sweeps, then processes the jobs that are due. Returns how many were processed."""
        await self.sweep()
        if (self._last_document_sweep is None
                or time.monotonic() - self._last_document_sweep >= self.document_sweep_seconds):
            await self.sweep_documents()
        jobs = await self.claim()
        for job in jobs:
            await self._process(job)
        return len(jobs)

    async def _run(self, job: Job) -> tuple[bool, float]:
        """Runs one job step: (done, seconds until the next attempt)."""
        async with SqlUnitOfWork(self.sessions) as uow:
            if job.kind == SYNC_JOB:
                result = await sync_booking(uow, self.provider, self.clock,
                                            UUID(job.payload["bookingId"]), self.policy,
                                            job.attempts)
                return result.done, result.retry_in
            if job.kind == GENERATE_JOB:
                generated = await generate_document(uow, self.storage, self.renderer,
                                                    self.clock,
                                                    UUID(job.payload["documentId"]))
                return generated.done, self.policy.retry_base_seconds
        raise ValueError(f"unknown job kind {job.kind!r}")

    async def _process(self, job: Job) -> None:
        # The job runs under the id of the request that created it, so its logs (and the
        # provider calls it makes) can be traced back to that request.
        token = request_id_var.set(job.request_id)
        try:
            await self._process_job(job)
        finally:
            request_id_var.reset(token)

    async def _process_job(self, job: Job) -> None:
        now = self.clock.now()
        try:
            done, retry_in = await self._run(job)
            if done:
                job.move(JobStatus.SUCCEEDED, now)
                job.last_error = None
            else:
                job.move(JobStatus.QUEUED, now)
                job.run_after = self.clock.now() + timedelta(seconds=retry_in)
        except Exception as exc:  # keep the worker alive; retry with backoff or give up
            logger.exception("%s: job %s (%s) failed request_id=%s", self.name, job.id,
                             job.kind, job.request_id)
            job.last_error = f"{type(exc).__name__}: {exc}"
            if job.kind == GENERATE_JOB and job.attempts >= job.max_attempts:
                job.move(JobStatus.FAILED, now)
                async with SqlUnitOfWork(self.sessions) as uow:
                    await give_up_generation(
                        uow, self.clock, UUID(job.payload["documentId"]),
                        f"Generation failed after {job.attempts} attempts: {job.last_error}")
            else:
                job.move(JobStatus.QUEUED, now)
                job.run_after = now + timedelta(
                    seconds=min(self.policy.retry_max_seconds,
                                self.policy.retry_base_seconds * 2 ** job.attempts))
        job.locked_until = None
        async with SqlUnitOfWork(self.sessions) as uow:
            await uow.jobs.save(job)
            await uow.commit()
        logger.info("%s: job %s %s -> %s (attempt %d) request_id=%s", self.name, job.id,
                    job.kind, job.status, job.attempts, job.request_id)

    async def run_forever(self, stop: asyncio.Event, poll_interval: float) -> None:
        logger.info("%s started", self.name)
        while not stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:  # database briefly unavailable, etc.
                logger.exception("%s: iteration failed", self.name)
                processed = 0
            if not processed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
        logger.info("%s stopped", self.name)
