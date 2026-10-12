"""The interfaces Attune offers, and which of them a user has turned on.

Each interface declares itself in its own package (`descriptor.py` says how);
adding one means listing it in `INTERFACE_CATALOG`. The settings API, the shell
launcher, artifact gating, the routes the app mounts and its background work all
read from that list. The catalog says what exists; the rest of this module says
what one user has:
the table behind that, the payload the settings page reads, and the dependency
that keeps a route off limits until the interface is enabled.

The modules this imports to build the catalog must not import it back at module
level; they reach it inside a function instead. The API for turning an interface
on or off is at the end.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, Session

from app.core.auth import get_current_user, User
from app.core.database import Base, get_db
from app.core.time import utc_now
from app.interfaces.calendar.interface import INTERFACE as CALENDAR
from app.interfaces.descriptor import InterfaceDescriptor


# === The registry ==================================================================

# --- The catalog -----------------------------------------------------------------

# Every interface Attune offers. Each declares itself in its own `interface.py`.
INTERFACE_CATALOG: tuple[InterfaceDescriptor, ...] = (CALENDAR,)


def get_interface(key: str) -> InterfaceDescriptor | None:
    return next(
        (descriptor for descriptor in INTERFACE_CATALOG if descriptor.key == key), None
    )


# --- One user's interfaces -------------------------------------------------------

class UserInterfacePayload(BaseModel):
    key: str
    name: str
    description: str
    enabled: bool


class InterfacesPayload(BaseModel):
    interfaces: list[UserInterfacePayload] = Field(default_factory=list)


class UserInterface(Base):
    """Whether one user has enabled one interface.

    A missing row means the interface is disabled, so every interface starts
    opt-in without needing a row per user per interface up front.
    """

    __tablename__ = "interfaces_preferences"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("auth_users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


# --- Enabled interfaces ----------------------------------------------------------

class UnknownInterfaceError(LookupError):
    pass


def _enabled_map(user_id: str, db: Session) -> dict[str, bool]:
    return {
        row.key: row.enabled
        for row in db.query(UserInterface).filter(UserInterface.user_id == user_id)
    }


def enabled_interface_keys(user_id: str, db: Session) -> set[str]:
    """Keys of the interfaces this user has turned on.

    Interfaces missing a row are disabled, and keys no longer in the catalog are
    ignored rather than reported as enabled.
    """
    enabled = _enabled_map(user_id, db)
    return {
        descriptor.key
        for descriptor in INTERFACE_CATALOG
        if enabled.get(descriptor.key, False)
    }


def interface_enabled(user_id: str, key: str, db: Session) -> bool:
    return key in enabled_interface_keys(user_id, db)


def require_interface_descriptor(key: str) -> InterfaceDescriptor:
    descriptor = get_interface(key)
    if descriptor is None:
        raise UnknownInterfaceError(f"Unknown interface: {key}.")
    return descriptor


def set_interface_enabled(user_id: str, key: str, enabled: bool, db: Session) -> None:
    require_interface_descriptor(key)
    row = (
        db.query(UserInterface)
        .filter(UserInterface.user_id == user_id, UserInterface.key == key)
        .first()
    )
    if row is None:
        db.add(UserInterface(user_id=user_id, key=key, enabled=enabled))
    else:
        row.enabled = enabled
    db.commit()


def _interface_payload(
    descriptor: InterfaceDescriptor, enabled: bool, user_id: str, db: Session
) -> UserInterfacePayload:
    return UserInterfacePayload(
        key=descriptor.key,
        name=descriptor.name,
        description=descriptor.description,
        enabled=enabled,
    )


def interfaces_payload(user_id: str, db: Session) -> InterfacesPayload:
    enabled = _enabled_map(user_id, db)
    return InterfacesPayload(
        interfaces=[
            _interface_payload(
                descriptor, enabled.get(descriptor.key, False), user_id, db
            )
            for descriptor in INTERFACE_CATALOG
        ]
    )


# --- Routes ----------------------------------------------------------------------

def require_interface(key: str) -> Callable[..., None]:
    """Reject requests to a surface the user has not enabled."""
    descriptor = get_interface(key)
    name = descriptor.name if descriptor else key

    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> None:
        if not interface_enabled(current_user.id, key, db):
            raise HTTPException(
                status_code=403,
                detail=f"Enable the {name} interface in settings to use it.",
            )

    return dependency


# === The API =======================================================================
#
# The interfaces API: what a user has, and turning one on or off.

# --- Contracts -------------------------------------------------------------------

class InterfaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


router = APIRouter(prefix="/api/interfaces", tags=["interfaces"])


@router.get("", response_model=InterfacesPayload)
def interfaces(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterfacesPayload:
    return interfaces_payload(current_user.id, db)


@router.put("/{interface_key}", response_model=InterfacesPayload)
def update_interface(
    interface_key: str,
    payload: InterfaceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterfacesPayload:
    try:
        require_interface_descriptor(interface_key)
    except UnknownInterfaceError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    set_interface_enabled(current_user.id, interface_key, payload.enabled, db)
    return interfaces_payload(current_user.id, db)
