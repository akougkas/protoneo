"""Owned session tasks: cancellation, terminal state, and cleanup in one place."""

import asyncio
import logging

from ..deliberation.session import SessionStatus
from ..llm.errors import sanitize_error_message

logger = logging.getLogger("protoneo.api.tasks")
_background_tasks: set[asyncio.Task] = set()
_stopping = False


def start_background_task(coroutine, *, name=None):
    task = asyncio.create_task(coroutine, name=name)
    _background_tasks.add(task)

    def finished(done):
        _background_tasks.discard(done)
        if not done.cancelled() and done.exception():
            logger.error("Background task failed: %s", sanitize_error_message(done.exception()))

    task.add_done_callback(finished)
    return task


def start_session_task(session_id, coroutine, *, sessions, bus, control, controls):
    started = False

    async def run():
        nonlocal started
        started = True
        try:
            await coroutine
        except asyncio.CancelledError:
            session = await sessions.get(session_id)
            if session:
                session.status = SessionStatus.STOPPED
                session.error = None
                await sessions.update(session)
            bus.emit("pipeline_cancelled", {"message": "Pipeline cancelled"})
            if _stopping:
                raise
        except Exception as exc:
            error = sanitize_error_message(exc)
            logger.exception("Session %s failed: %s", session_id, error)
            session = await sessions.get(session_id)
            if session:
                session.status, session.error = SessionStatus.FAILED, error
                step = session.pipeline_steps.get(control.current_step)
                if isinstance(step, dict):
                    step.update(status="failed", error=error)
                await sessions.update(session)
            bus.emit("error", {"detail": error})
        finally:
            if controls.get(session_id) is control:
                controls.pop(session_id, None)

    task = start_background_task(run(), name=f"session:{session_id}")

    def cancelled_before_start(done):
        if started:
            return
        coroutine.close()

        async def finalize():
            session = await sessions.get(session_id)
            if session:
                session.status, session.error = SessionStatus.STOPPED, None
                await sessions.update(session)
            if controls.get(session_id) is control:
                controls.pop(session_id, None)
            bus.emit("pipeline_cancelled", {"message": "Pipeline cancelled"})

        start_background_task(finalize())

    task.add_done_callback(cancelled_before_start)
    control.set_task(task)
    controls[session_id] = control
    return task


async def stop_session_tasks():
    global _stopping
    _stopping = True
    try:
        tasks = list(_background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Include finalizers scheduled for tasks cancelled before their first turn.
        while True:
            await asyncio.sleep(0)
            pending = [task for task in _background_tasks if not task.done()]
            _background_tasks.intersection_update(pending)
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        _stopping = False
