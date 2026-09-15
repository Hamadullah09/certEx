"""Celery entry point: ``celery -A worker.celery_app worker``.

The application object itself is defined in :mod:`certex.queue` so the API can
enqueue work without importing worker code. This module's job is to import the
task modules - importing is what registers a task with the app - and to install
the worker-process lifecycle hooks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from celery import Celery
from celery.signals import (
    setup_logging,
    task_prerun,
    task_retry,
    worker_process_init,
    worker_ready,
)

from certex.config import get_settings
from certex.logging_setup import (
    bind_request_context,
    clear_request_context,
    configure_logging,
    get_logger,
)
from certex.queue import celery_app

if TYPE_CHECKING:  # pragma: no cover
    from celery import Task

__all__ = ["app", "celery_app"]

logger = get_logger(__name__)

# Importing the task modules registers them. Kept as an explicit list rather than
# autodiscovery so that a typo in a module name fails loudly at worker start
# instead of silently leaving a queue with no consumer.
celery_app.conf.imports = ("worker.tasks",)

app: Celery = celery_app


@setup_logging.connect
def _configure_worker_logging(**_kwargs: object) -> None:
    """Replace Celery's logging setup with the redacting configuration.

    Without this hook Celery installs its own handlers, and task arguments would
    be rendered by a formatter that has never heard of the PII policy.
    """
    configure_logging(get_settings(), force=True)


@worker_process_init.connect
def _init_worker_process(**_kwargs: object) -> None:
    """Reset inherited state in a freshly forked worker process.

    A prefork child inherits the parent's SQLAlchemy engine, including its open
    sockets. Two processes sharing one connection corrupt the wire protocol, so
    the cached engines are discarded here and rebuilt lazily per process.
    """
    from certex.db.session import get_async_engine, get_sync_engine

    get_sync_engine.cache_clear()
    get_async_engine.cache_clear()
    configure_logging(get_settings(), force=True)


@worker_ready.connect
def _on_worker_ready(**_kwargs: object) -> None:
    settings = get_settings()
    logger.info(
        "worker.ready",
        queue=",".join(sorted(str(queue.name) for queue in settings_queues())),
        provider=settings.llm_provider.value,
    )


def settings_queues() -> tuple[object, ...]:
    return tuple(celery_app.conf.task_queues or ())


@task_prerun.connect
def _bind_task_context(
    task_id: str | None = None,
    task: Task | None = None,
    **_kwargs: object,
) -> None:
    """Put the task id and name on every log record the task emits."""
    clear_request_context()
    bind_request_context(
        task_id=task_id or "",
        task_name=getattr(task, "name", "") or "",
    )


@task_retry.connect
def _log_retry(
    request: object = None,
    reason: object = None,
    **_kwargs: object,
) -> None:
    logger.warning(
        "worker.task_retry",
        task_name=getattr(request, "task", None),
        task_id=getattr(request, "id", None),
        retry=getattr(request, "retries", None),
        error_type=type(reason).__name__ if reason is not None else None,
    )
