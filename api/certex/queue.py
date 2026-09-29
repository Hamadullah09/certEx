"""Celery application and queue topology.

Defined in the shared package rather than in ``/worker`` so the API can enqueue
work by name without importing worker code - and so there is exactly one place
where queue names, serialisers and retry policy are declared.

Queue layout
------------
OCR is CPU-bound and can occupy a core for seconds per page. Left on a shared
queue it starves everything else, so it gets a dedicated queue served by a worker
class with its own concurrency ceiling (see ``WORKER_ROLE=ocr``). The remaining
stages are IO- or memory-bound and share the general worker.
"""

from __future__ import annotations

from typing import Final

from celery import Celery
from kombu import Queue

from certex.config import Settings, get_settings

__all__ = [
    "QUEUE_CLASSIFY",
    "QUEUE_DEFAULT",
    "QUEUE_EXPORT",
    "QUEUE_EXTRACT",
    "QUEUE_IMPORT",
    "QUEUE_INGEST",
    "QUEUE_MAINTENANCE",
    "QUEUE_OCR",
    "QUEUE_TEXT",
    "QUEUE_VALIDATE",
    "celery_app",
    "create_celery_app",
]

QUEUE_DEFAULT: Final = "default"
QUEUE_INGEST: Final = "ingest"
QUEUE_TEXT: Final = "text"
QUEUE_OCR: Final = "ocr"
QUEUE_CLASSIFY: Final = "classify"
QUEUE_EXTRACT: Final = "extract"
QUEUE_VALIDATE: Final = "validate"
QUEUE_EXPORT: Final = "export"
QUEUE_IMPORT: Final = "import"
"""Its own queue: one three-hundred-megabyte CSV occupies a worker for minutes, and
behind the export queue it would hold up every download in the office."""

QUEUE_MAINTENANCE: Final = "maintenance"

_ALL_QUEUES: Final[tuple[str, ...]] = (
    QUEUE_DEFAULT,
    QUEUE_INGEST,
    QUEUE_TEXT,
    QUEUE_OCR,
    QUEUE_CLASSIFY,
    QUEUE_EXTRACT,
    QUEUE_VALIDATE,
    QUEUE_EXPORT,
    QUEUE_IMPORT,
    QUEUE_MAINTENANCE,
)

# Task name prefix -> queue. Task names follow ``pipeline.<stage>.<action>``.
_ROUTES: Final[dict[str, dict[str, str]]] = {
    "pipeline.ingest.*": {"queue": QUEUE_INGEST},
    "pipeline.normalize.*": {"queue": QUEUE_INGEST},
    "pipeline.text.*": {"queue": QUEUE_TEXT},
    "pipeline.ocr.*": {"queue": QUEUE_OCR},
    "pipeline.split.*": {"queue": QUEUE_CLASSIFY},
    "pipeline.classify.*": {"queue": QUEUE_CLASSIFY},
    "pipeline.extract.*": {"queue": QUEUE_EXTRACT},
    "pipeline.validate.*": {"queue": QUEUE_VALIDATE},
    "pipeline.finalize.*": {"queue": QUEUE_VALIDATE},
    "export.*": {"queue": QUEUE_EXPORT},
    "register.import.*": {"queue": QUEUE_IMPORT},
    "maintenance.*": {"queue": QUEUE_MAINTENANCE},
}


def create_celery_app(settings: Settings | None = None) -> Celery:
    active = settings or get_settings()
    app = Celery("certex", broker=active.celery_broker_url, backend=active.celery_result_backend)

    app.conf.update(
        # -- serialisation: JSON only. Pickle would let a compromised broker
        # execute arbitrary code inside a worker holding document data.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # -- routing
        task_queues=tuple(Queue(name) for name in _ALL_QUEUES),
        task_default_queue=QUEUE_DEFAULT,
        task_routes=_ROUTES,
        # -- reliability: acknowledge only after the task returns, so a worker
        # killed mid-page redelivers that page instead of silently dropping it.
        # Every task is idempotent, which is what makes redelivery safe.
        task_acks_late=True,
        task_acks_on_failure_or_timeout=False,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        # -- limits
        task_time_limit=active.celery_task_time_limit,
        task_soft_time_limit=active.celery_task_soft_time_limit,
        worker_max_tasks_per_child=active.celery_max_tasks_per_child,
        # -- retries
        task_default_retry_delay=active.celery_retry_backoff_seconds,
        task_publish_retry=True,
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            # Redelivery window for an unacknowledged message. Must exceed the
            # hard task limit or a long OCR job would be delivered twice.
            "visibility_timeout": active.celery_task_time_limit + 600,
            "max_retries": 5,
        },
        # -- results
        result_expires=86_400,
        result_extended=True,
        # -- observability
        worker_send_task_events=True,
        task_send_sent_event=True,
        task_track_started=True,
    )
    return app


celery_app: Celery = create_celery_app()
