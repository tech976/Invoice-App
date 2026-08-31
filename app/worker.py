"""Background extraction worker.

An upload has to return immediately — reading a bill takes tens of seconds —
so uploads enqueue a Job row and these threads drain the queue. Keeping the
queue in the database rather than in memory means a restart mid-batch resumes
where it left off instead of silently dropping work.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.extraction.pipeline import process_document
from app.models import Document, Job

log = logging.getLogger(__name__)

_stop = threading.Event()
# Set when a job is enqueued, so a waiting worker starts at once instead of
# discovering the work on its next sweep. The sweep stays as a backstop: the
# enqueue happens inside a transaction that has not committed yet, so a woken
# worker may look a moment too early and find nothing.
_wake = threading.Event()
_threads: list[threading.Thread] = []
POLL_SECONDS = 2.0


def enqueue(db, document_id: int, kind: str = "extract") -> Job:
    job = Job(kind=kind, document_id=document_id, status="queued")
    db.add(job)
    db.flush()
    _wake.set()
    return job


def _claim_job() -> int | None:
    """Take the oldest queued job. Row-locked so two threads never share one."""
    with session_scope() as db:
        stmt = (
            select(Job)
            .where(Job.status == "queued")
            .order_by(Job.created_at)
            .limit(1)
        )
        if db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        job = db.scalar(stmt)
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.started_at = datetime.now(timezone.utc)
        return job.id


def _run_job(job_id: int) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        document_id = job.document_id

    retryable = True
    try:
        with session_scope() as db:
            process_document(db, document_id)
        error = None
    except Exception as exc:  # noqa: BLE001 - the failure is recorded, not raised
        error = str(exc)[:2000]
        retryable = getattr(exc, "retryable", True)
        log.error("job %s failed%s: %s", job_id, "" if retryable else " (permanent)", error)

    # A separate session, because the one above was rolled back by the failure
    # — anything written inside it, including the document's failed status,
    # went with it.
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.finished_at = datetime.now(timezone.utc)
        job.last_error = error

        if error is None:
            job.status = "done"
            return

        if retryable and job.attempts < job.max_attempts:
            job.status = "queued"
            log.info("job %s requeued (attempt %s/%s)", job_id, job.attempts, job.max_attempts)
            return

        job.status = "failed"
        if job.document_id:
            doc = db.get(Document, job.document_id)
            if doc is not None:
                doc.status = "failed"
                doc.error_message = error


def _loop(name: str) -> None:
    log.info("worker %s started", name)
    while not _stop.is_set():
        try:
            job_id = _claim_job()
        except Exception:  # noqa: BLE001 - never let the loop die
            log.exception("worker %s could not claim a job", name)
            job_id = None

        if job_id is None:
            _wake.wait(POLL_SECONDS)
            _wake.clear()
            if _stop.is_set():
                break
            continue
        _run_job(job_id)
    log.info("worker %s stopped", name)


def start_workers() -> None:
    if _threads:
        return
    _stop.clear()
    for i in range(max(1, settings.worker_threads)):
        thread = threading.Thread(target=_loop, args=(f"w{i + 1}",), daemon=True)
        thread.start()
        _threads.append(thread)


def stop_workers(timeout: float = 5.0) -> None:
    _stop.set()
    deadline = time.time() + timeout
    for thread in _threads:
        thread.join(max(0.0, deadline - time.time()))
    _threads.clear()


def requeue_stuck(minutes: int = 30) -> int:
    """Return jobs orphaned by a crash to the queue. Called at startup."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    with session_scope() as db:
        stuck = db.scalars(
            select(Job).where(Job.status == "running", Job.started_at < cutoff)
        ).all()
        for job in stuck:
            job.status = "queued"
            if job.document_id:
                doc = db.get(Document, job.document_id)
                if doc and doc.status == "processing":
                    doc.status = "queued"
        return len(stuck)
