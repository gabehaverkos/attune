"""What an interface declares about itself.

Each interface declares an `InterfaceDescriptor` in its own `interface.py`. The
registry catalogs the interfaces, and everything generic (settings, gating, the
agent's instructions, memory's outline, mounting routes) reads from there.

The registry imports every declaration, so a declaration must not import the
registry back at module level: what would (an interface's router) is named by a
function that imports it when called.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter


@dataclass(frozen=True)
class InterfaceDescriptor:
    key: str
    name: str
    description: str
    agent_instructions: str = ""
    # What opening it lets the agent do, finishing "open the calendar to …"; the
    # agent reads it to decide when to open it.
    tools_summary: str = ""
    # Its API, mounted by the app. A function, because the router depends on the
    # registry, which imports this declaration.
    router: Callable[[], APIRouter] | None = None
