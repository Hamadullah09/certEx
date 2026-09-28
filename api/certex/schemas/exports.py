"""What a download will contain, shown before it is downloaded.

The point of the preview is the unreviewed count. Exporting a batch that nobody has
checked is a legitimate thing to want - an office may be taking the machine's first pass
to work from - but it should be a decision rather than a surprise, especially when the
file is about to be emailed to someone else.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from certex.enums import CertificateType

__all__ = ["ExportPreview"]


class ExportPreview(BaseModel):
    """A summary of the export the current filters would produce."""

    model_config = ConfigDict(frozen=True)

    batch_id: uuid.UUID
    row_count: int = Field(ge=0, description="Rows the download would contain.")
    column_count: int = Field(ge=0)
    unreviewed_count: int = Field(
        ge=0,
        description="Rows in this download that no person has checked or approved yet.",
    )
    certificate_types: list[CertificateType] = Field(
        default_factory=list, description="The kinds of certificate present."
    )
    columns: list[str] = Field(
        default_factory=list, description="Column headers, in the order they will appear."
    )
    filename: str = Field(description="What the downloaded file will be called.")
