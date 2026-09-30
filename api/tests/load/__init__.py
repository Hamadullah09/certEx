"""Tests about scale: what happens to the register when it is full.

Kept apart from the integration suite because they build hundreds of thousands of rows
and take minutes. Run them deliberately:

    pytest tests/load -m load
    CERTEX_SCALE_ROWS=100000 pytest tests/load -m load
    CERTEX_SCALE_ROWS=1000000 pytest tests/load -m load
"""
