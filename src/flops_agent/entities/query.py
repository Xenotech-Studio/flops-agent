"""A contribution added to one agent turn.

Regeneration and continuation are represented by session state, not query
flags. A query exists only when a caller contributes new user input, an answer,
or an external event.
"""
from __future__ import annotations

from typing_extensions import override
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


def _empty_dict_list() -> List[Dict[str, Any]]:
    return []


def _empty_dict() -> Dict[str, Any]:
    return {}


class Contributor(str, Enum):
    """The source of a contribution and its history semantics."""

    USER = "user"
    """A normal user message."""

    ANSWER = "answer"
    """A structured answer to a pending interaction."""

    SYSTEM_EVENT = "system_event"
    """An autonomous turn triggered by an external system event."""


@dataclass
class Query:
    """The content and metadata contributed to one turn."""

    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    by: Contributor = Contributor.USER
    attachments: List[Dict[str, Any]] = field(default_factory=_empty_dict_list)
    refs: List[Dict[str, Any]] = field(default_factory=_empty_dict_list)
    metadata: Dict[str, Any] = field(default_factory=_empty_dict)

    @property
    def is_empty(self) -> bool:
        """Whether this query contributes no new material."""
        if self.attachments or self.refs:
            return False
        if isinstance(self.content, str):
            return not self.content.strip()
        return not self.content

    @property
    def is_user_initiated(self) -> bool:
        """Whether this turn originated with a user-facing request."""
        return self.by is not Contributor.SYSTEM_EVENT

    # Common constructors.

    @classmethod
    def text(cls, text: str, **kw: Any) -> "Query":
        """Create a normal user-text contribution."""
        return cls(content=text, **kw)

    @classmethod
    def answer(cls, content: Any, **kw: Any) -> "Query":
        """Create an answer to a pending interaction."""
        return cls(content=content, by=Contributor.ANSWER, **kw)

    @classmethod
    def event(cls, content: Any, **kw: Any) -> "Query":
        """Create a contribution from an external system event."""
        return cls(content=content, by=Contributor.SYSTEM_EVENT, **kw)

    @override
    def __repr__(self) -> str:
        if isinstance(self.content, str):
            preview = self.content[:40].replace("\n", " ")
        elif self.content:
            preview = f"<{len(self.content)} blocks>"
        else:
            preview = ""
        extra = "".join(
            s for s in (
                f" +{len(self.attachments)}att" if self.attachments else "",
                f" +{len(self.refs)}ref" if self.refs else "",
            )
        )
        return f"<Query by={self.by.value} {preview!r}{extra}>"


__all__ = ["Query", "Contributor"]
