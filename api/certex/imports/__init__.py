"""Loading an existing register in from a CSV, and the template it fills.

Reading and mapping only: nothing here touches the database, which is what lets the
whole path be tested against a string and reused by the worker that streams a
three-hundred-megabyte file out of object storage.
"""

from certex.imports.reader import (
    HeaderPlan,
    RowRead,
    decode_stream,
    detect_delimiter,
    plan_headers,
    read_rows,
)
from certex.imports.template import template_csv, template_headers

__all__ = [
    "HeaderPlan",
    "RowRead",
    "decode_stream",
    "detect_delimiter",
    "plan_headers",
    "read_rows",
    "template_csv",
    "template_headers",
]
