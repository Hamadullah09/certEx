"""Celery task wrappers for the pipeline stages.

Each stage is a separate task that reads its input from, and writes its output to,
the database. That is what makes the pipeline resumable after a crash and lets an
individual stage be re-run without redoing the whole document.
"""

from __future__ import annotations

__all__: list[str] = []
