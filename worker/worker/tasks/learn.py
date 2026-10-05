"""Stage 9 task: learn an office's form from a correction a reviewer just made.

Never retried for anything but infrastructure. Learning is best-effort by design - the
certificate the reviewer corrected is already right, and a form that teaches nothing
today teaches something the next time somebody corrects it - so a task that cannot find
a rule simply ends.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import InterfaceError, OperationalError

from certex.core.errors import StorageUnavailableError
from certex.pipeline.dispatch import TASK_LEARN_TEMPLATE
from certex.pipeline.stages.learn_stage import run_learn_template_stage
from certex.queue import celery_app

__all__ = ["learn_template"]

TRANSIENT_ERRORS = (StorageUnavailableError, OperationalError, InterfaceError)


@celery_app.task(
    name=TASK_LEARN_TEMPLATE,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def learn_template(unit_id: str) -> dict[str, object]:
    result = run_learn_template_stage(uuid.UUID(unit_id))
    return {
        "ran": result.ran,
        "template_id": str(result.template_id) if result.template_id else None,
        "learned": result.learned,
        "created": result.created,
    }
