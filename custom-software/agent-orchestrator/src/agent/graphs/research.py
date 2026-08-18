"""Research graph — dispatches long-running work to the Celery worker.

The HTTP caller gets a task_id immediately. The Celery worker runs the actual
parallel-search + synthesis pipeline and delivers the result via
mcp-notifications (push/webui) when done.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


async def dispatch_research(
    message: str,
    user_id: str,
    scope: str,
    thread_id: str,
    task_id: str,
    tools_needed: list[str],
) -> None:
    """Enqueue a research task on the Celery broker.

    Silently degrades when Celery is unavailable (e.g. during Phase 1 when
    the worker container is not running) so the rest of the stack is unaffected.
    Results would simply not arrive — the task stays in ``queued`` state until
    the worker comes up, or the user cancels it.
    """
    try:
        from agent.tasks import run_research  # imported lazily: Celery may not be configured

        run_research.delay(
            message=message,
            user_id=user_id,
            scope=scope,
            thread_id=thread_id,
            task_id=task_id,
            tools_needed=tools_needed,
        )
        log.info(
            "research_dispatched",
            task_id=task_id,
            thread_id=thread_id,
            user_id=user_id,
        )
    except ImportError:
        log.warning("celery_unavailable", task_id=task_id)
    except Exception as exc:  # noqa: BLE001
        log.error("research_dispatch_failed", task_id=task_id, error=str(exc))
