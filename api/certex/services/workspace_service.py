"""Reading and writing a workspace's settings.

Stored as JSON on the workspace row rather than as columns, because these are choices
an office makes about how it works and they will keep changing shape; a validated
Pydantic model at the boundary is what keeps that from meaning "anything goes".

The synchronous twin exists for the workers: the validate stage needs the office's
thresholds, and it has no event loop.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from certex.core.deps import WorkspaceScope
from certex.core.errors import BadRequestError, NotFoundError
from certex.db.models import Workspace
from certex.enums import UserRole
from certex.logging_setup import get_logger
from certex.schemas.workspace import ReviewSettings, WorkspaceSettings, WorkspaceSettingsUpdate

__all__ = [
    "SETTINGS_KEY",
    "read_settings",
    "rename_workspace",
    "review_settings_sync",
    "update_settings",
]

logger = get_logger(__name__)

SETTINGS_KEY = "settings"
"""Top-level key inside ``workspaces.settings_json``.

Namespaced rather than stored at the root so anything else that ends up on this row -
a feature flag, a branding choice - cannot collide with a settings section.
"""


def _parse(stored: object) -> WorkspaceSettings:
    """Read stored settings, falling back to the defaults for anything unreadable.

    Deliberately forgiving in this direction: a settings blob written by an older
    version, or one hand-edited into nonsense, must not stop an office reading its
    register. Writing is strict, so nonsense cannot get in this way.
    """
    if isinstance(stored, dict):
        try:
            return WorkspaceSettings.model_validate(stored)
        except ValueError:
            logger.warning("workspace.settings_unreadable")
    return WorkspaceSettings()


async def read_settings(session: AsyncSession, *, workspace_id: uuid.UUID) -> WorkspaceSettings:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError("That workspace does not exist.")
    return _parse(workspace.settings_json.get(SETTINGS_KEY))


async def update_settings(
    session: AsyncSession, *, scope: WorkspaceScope, update: WorkspaceSettingsUpdate
) -> WorkspaceSettings:
    """Change the settings. Sections the caller left out stay as they were.

    A partial update rather than a replacement, because two administrators with the
    same page open would otherwise overwrite each other's sections without either of
    them touching the other's fields.
    """
    scope.require(UserRole.ADMIN)

    workspace = await session.get(Workspace, scope.workspace_id)
    if workspace is None:  # pragma: no cover - the scope proves it exists
        raise NotFoundError("That workspace does not exist.")

    current = _parse(workspace.settings_json.get(SETTINGS_KEY))
    merged = WorkspaceSettings(review=update.review or current.review)

    # Reassigned rather than mutated in place: SQLAlchemy does not see a change
    # inside a JSONB dict, and the update would be silently dropped.
    settings = dict(workspace.settings_json)
    settings[SETTINGS_KEY] = merged.model_dump(mode="json")
    workspace.settings_json = settings
    await session.flush()

    logger.info(
        "workspace.settings_updated",
        workspace_id=str(scope.workspace_id),
        user_id=str(scope.user_id),
    )
    return merged


def review_settings_sync(session: SyncSession, workspace_id: uuid.UUID) -> ReviewSettings:
    """The office's review thresholds, for a worker deciding how to route a row."""
    stored = session.scalar(select(Workspace.settings_json).where(Workspace.id == workspace_id))
    if not isinstance(stored, dict):
        return ReviewSettings()
    return _parse(stored.get(SETTINGS_KEY)).review


async def rename_workspace(session: AsyncSession, *, scope: WorkspaceScope, name: str) -> Workspace:
    """Change what this office is called.

    It appears on every screen and in the name of every file exported, and the one a
    deployment starts with is whatever the seeding script was given - "Demo Records
    Office" unless somebody set CERTEX_SEED_WORKSPACE_NAME. An office that cannot
    change it is stuck introducing itself as a demo.
    """
    scope.require(UserRole.ADMIN)
    cleaned = " ".join(name.split())
    if not cleaned:
        raise BadRequestError(
            "Give this office a name.",
            title="Name is empty",
            remediation="Type the name of the office or department.",
        )

    workspace = await session.get(Workspace, scope.workspace_id)
    if workspace is None:  # pragma: no cover - the scope proves it exists
        raise NotFoundError("This workspace no longer exists.")

    workspace.name = cleaned[:200]
    await session.flush()
    logger.info("workspace.renamed", workspace_id=str(scope.workspace_id))
    return workspace
