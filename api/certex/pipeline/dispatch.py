"""Enqueue pipeline stages by task name.

The API and the stages refer to tasks by name only, so this module - not the worker
package - is the one place task names are spelled. Routing to queues comes from
:mod:`certex.queue`.

When the task is registered in the current process (inside a worker, or a test
that imports the worker's task modules) it is sent through its own task object,
which honours eager execution. Otherwise - in the API - it goes out by name.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from certex.logging_setup import get_logger
from certex.queue import celery_app

__all__ = [
    "TASK_CLASSIFY_UNIT",
    "TASK_EXTRACT_UNIT",
    "TASK_FINALIZE_DOCUMENT",
    "TASK_IMPORT_CSV",
    "TASK_OCR_PAGE",
    "TASK_SPLIT_DOCUMENT",
    "TASK_TEXT_DOCUMENT",
    "TASK_VALIDATE_UNIT",
    "Dispatch",
    "TaskArgument",
    "enqueue",
]

logger = get_logger(__name__)

TASK_TEXT_DOCUMENT: Final = "pipeline.text.extract_document"
TASK_OCR_PAGE: Final = "pipeline.ocr.page"
TASK_SPLIT_DOCUMENT: Final = "pipeline.split.document"
TASK_CLASSIFY_UNIT: Final = "pipeline.classify.unit"
TASK_EXTRACT_UNIT: Final = "pipeline.extract.unit"
TASK_VALIDATE_UNIT: Final = "pipeline.validate.unit"
TASK_FINALIZE_DOCUMENT: Final = "pipeline.finalize.document"
TASK_IMPORT_CSV: Final = "register.import.csv"

TaskArgument = str | int
Dispatch = Callable[[str, dict[str, TaskArgument]], None]
"""Signature of an enqueue function; stages accept one so a caller can redirect them."""


def enqueue(task_name: str, kwargs: dict[str, TaskArgument]) -> None:
    """Send one task. Arguments are ids and page numbers only - never document content."""
    task = celery_app.tasks.get(task_name)
    if task is not None:
        task.apply_async(kwargs=kwargs)
    else:
        celery_app.send_task(task_name, kwargs=kwargs)
    logger.info("pipeline.enqueued", task_name=task_name)
