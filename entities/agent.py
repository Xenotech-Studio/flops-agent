"""Assistant identity, model preference, and optional long-term memory.

An agent is independent from runtime infrastructure so one deployment can serve
multiple personas. Memory is read while building a request and updated only
after subscribers have received the completed turn.
"""
from __future__ import annotations

from typing_extensions import override
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from flops_agent.entities.query import Query
from flops_agent.entities.session import Session

logger = logging.getLogger(__name__)


def _empty_dict() -> Dict[str, Any]:
    return {}


@runtime_checkable
class Memory(Protocol):
    """Product-owned memory storage with framework-defined call timing."""

    async def recall(self, session: Optional[Session], *, query: Optional[Query] = None) -> str:
        """Return memory relevant to the current request, or an empty string."""
        ...

    async def remember(self, session: Session) -> None:
        """Update memory after a turn without blocking its completion."""
        ...


@dataclass
class Agent:
    """Identity data and memory hooks for one assistant."""

    id: str = ""
    name: str = ""

    instructions: str = ""
    """Persona instructions included in the system prompt."""

    model: Optional[str] = None
    """Optional model preference; defaults to ``Runtime.model``."""

    memory: Optional[Memory] = None

    metadata: Dict[str, Any] = field(default_factory=_empty_dict)
    """Product-defined metadata that the framework does not interpret."""

    # Text supplied to the runner.

    async def persona(self, session: Optional[Session], *, query: Optional[Query] = None) -> str:
        """Return persona text for this request; subclasses may load it dynamically."""
        return self.instructions

    async def recall(self, session: Optional[Session], *, query: Optional[Query] = None) -> str:
        """Read memory, degrading to an empty string when unavailable."""
        if self.memory is None:
            return ""
        try:
            return await self.memory.recall(session, query=query) or ""
        except Exception:
            logger.exception("agent %s recall failed", self.id or self.name)
            return ""

    def schedule_remember(self, session: Session) -> "Optional[asyncio.Task[None]]":
        """Schedule a post-turn memory update and return immediately."""
        memory = self.memory
        if memory is None:
            return None

        async def _run() -> None:
            try:
                await memory.remember(session)
            except Exception:
                # Memory failure must not turn a successful reply into an error.
                logger.exception("agent %s remember failed", self.id or self.name)

        try:
            return asyncio.get_running_loop().create_task(_run())
        except RuntimeError:
            return None  # No event loop in a synchronous context.

    @override
    def __repr__(self) -> str:
        bits = [self.name or self.id or "unnamed"]
        if self.model:
            bits.append(self.model)
        if self.memory is not None:
            bits.append("memory")
        return f"<Agent {' '.join(bits)}>"


__all__ = ["Agent", "Memory"]
