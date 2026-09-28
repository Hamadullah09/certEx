"""The shared clock must never repeat or run backwards within a process.

``created_at`` drives every "newest first" list and every keyset cursor. On Windows
the wall clock advances in ~15 ms steps, so thousands of consecutive readings are
identical; before this guarantee, rows inserted in quick succession tied and came
back in arbitrary order.
"""

from __future__ import annotations

import datetime as dt
import itertools
import threading

import pytest

from certex.db.base import utcnow

pytestmark = pytest.mark.unit


def test_consecutive_readings_strictly_increase() -> None:
    readings = [utcnow() for _ in range(5000)]
    assert all(later > earlier for earlier, later in itertools.pairwise(readings))


def test_readings_are_timezone_aware_utc() -> None:
    assert utcnow().tzinfo is dt.UTC


def test_readings_track_the_wall_clock() -> None:
    """Nudging past ties must not drift the timestamp away from real time."""
    before = dt.datetime.now(dt.UTC)
    reading = utcnow()
    after = dt.datetime.now(dt.UTC)
    assert before - dt.timedelta(seconds=1) <= reading <= after + dt.timedelta(seconds=1)


def test_unique_across_threads() -> None:
    """Worker threads share the clock; none may observe another's timestamp."""
    collected: list[dt.datetime] = []
    lock = threading.Lock()

    def sample() -> None:
        local = [utcnow() for _ in range(1000)]
        with lock:
            collected.extend(local)

    threads = [threading.Thread(target=sample) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(collected)) == len(collected)
