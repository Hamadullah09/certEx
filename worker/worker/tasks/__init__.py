"""Celery task wrappers for the pipeline stages.

Each stage is a separate task that reads its input from, and writes its output to,
the database. That is what makes the pipeline resumable after a crash and lets an
individual stage be re-run without redoing the whole document.

Importing this package registers every task with the Celery app.
"""

from __future__ import annotations

from worker.tasks import extract, ocr, split, text, validate

__all__ = ["extract", "ocr", "split", "text", "validate"]
