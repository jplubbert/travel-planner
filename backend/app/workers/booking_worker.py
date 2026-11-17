"""Background worker: runs durable jobs from the ``jobs`` table (booking reconciliation).

Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` and a lease, so several workers can run
safely: a job is processed by one worker at a time, and a job whose worker died is picked
up again when its lease expires.
"""

import asyncio
import contextlib
import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.sqlalchemy.uow import SqlUnitOfWork
from app.domain.jobs import Job, JobStatus
from app.domain.reservations import (
    SYNC_JOB,
    ReconcilePolicy,
    sweep_stale_submitted,
    sync_booking,
)
from app.ports.clock import Clock
from app.ports.provider import ProviderClient

logger = logging.getLogger("app.worker")


class BookingWorker:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], provider: ProviderClient,
        clock: Clock, policy: ReconcilePolicy, *, lease_seconds: float = 120.0,
        batch_size: int = 10, name: str = "worker",
    ) -> None:
        self.sessions = sessions
        self.provider = provider
        self.clock = clock
        self.policy = policy
        self.lease = timedelta(seconds=lease_seconds)
        self.batch_size = batch_size
        self.name = name

    async def sweep(self) -> int:
        """Finds ``submitted`` bookings whose sender stopped and schedules them."""
        async with SqlUnitOfWork(self.sessions) as uow:
            count = await sweep_stale_submitted(uow, self.clock, self.policy)
        if count:
            logger.warning("%s: %d interrupted booking submission(s) scheduled for "
                           "reconciliation", self.name, count)
        return count

    async def claim(self) -> list[Job]:
        now = self.clock.now()
        async with SqlUnitOfWork(self.sessions) as uow:
            jobs = await uow.jobs.claim(now, now + self.lease, self.batch_size)
            await uow.commit()
        return jobs

    async def run_once(self) -> int:
        """Sweeps, then processes the jobs that are due. Returns how many were processed."""
        await self.sweep()
        jobs = await self.claim()
        for job in jobs:
            await self._process(job)
        return len(jobs)

    async def _process(self, job: Job) -> None:
        now = self.clock.now()
        try:
            if job.kind != SYNC_JOB:
                raise ValueError(f"unknown job kind {job.kind!r}")
            async with SqlUnitOfWork(self.sessions) as uow:
                result = await sync_booking(uow, self.provider, self.clock,
                                            UUID(job.payload["bookingId"]), self.policy,
                                            job.attempts)
            if result.done:
                job.move(JobStatus.SUCCEEDED, now)
                job.last_error = None
            else:
                job.move(JobStatus.QUEUED, now)
                job.run_after = self.clock.now() + timedelta(seconds=result.retry_in)
        except Exception as exc:  # keep the worker alive; the job is retried with backoff
            logger.exception("%s: job %s (%s) failed", self.name, job.id, job.kind)
            job.move(JobStatus.QUEUED, now)
            job.last_error = f"{type(exc).__name__}: {exc}"
            job.run_after = now + timedelta(
                seconds=min(self.policy.retry_max_seconds,
                            self.policy.retry_base_seconds * 2 ** job.attempts))
        job.locked_until = None
        async with SqlUnitOfWork(self.sessions) as uow:
            await uow.jobs.save(job)
            await uow.commit()
        logger.info("%s: job %s %s -> %s (attempt %d)", self.name, job.id, job.kind,
                    job.status, job.attempts)

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
