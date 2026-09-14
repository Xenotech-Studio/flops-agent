"""Session data and operations over its own history.

Sessions contain no runtime reference and are restored from persistence for each
request. History edits are addressed by stable message ids or user-turn ordinal,
and destructive edits require explicit consent when configured to remove user
authored content.
"""
from __future__ import annotations

import json

from typing_extensions import override
from typing import Any, Dict, List, Optional, cast


class MessageNotFound(LookupError):
    """A requested message id is absent, usually because client state is stale."""


class TruncationNeedsConsent(Exception):
    """A truncation would remove user-authored messages without consent."""

    def __init__(
        self,
        *,
        message_id: str,
        dropped: int,
        dropped_user_messages: int,
        total: int,
    ):
        self.message_id = message_id
        self.dropped = dropped
        self.dropped_user_messages = dropped_user_messages
        self.total = total
        super().__init__(
            f"truncating at {message_id!r} removes {dropped} messages, including "
            f"{dropped_user_messages} user messages, out of {total}; retry with consent=True"
        )


class Session:
    """One session; products may subclass its storage-facing shape."""

    #: External-message identifier field. Products map their wire schema here.
    external_id_field = "external_id"

    #: Boolean marker for a system-supplied user-shaped message. Products map
    #: their own message schema here.
    system_marker_field = "is_system"

    #: Metadata field that stores the title.
    title_field = "title"

    #: Active-run metadata, written and cleared by the framework lifecycle.
    active_run_field = "active_run_id"
    active_run_started_field = "active_run_started_at"

    #: Persistent marker for an interaction awaiting a user response.
    #:
    #: Suspension is represented by both a run event and this persisted marker.
    #: Persistent pending-interaction marker, maintained by the framework.
    suspended_field = "pending_interaction"
    #: Persistent paths of tool packages opened in this session.
    opened_packages_field = "opened_packages"
    #: Product-managed package overlays merged into the effective package set.
    overlay_packages_field = "package_overlays"
    #: Product-defined action names that change package visibility. None means
    #: this session has no history-replay convention for that action.
    open_package_action_name: Optional[str] = None
    close_package_action_name: Optional[str] = None

    def __init__(
        self,
        session_id: str,
        *,
        owner_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
        guard_user_message_loss: bool = False,
    ):
        """
        Args:
            guard_user_message_loss: Require ``consent=True`` before a truncation
                removes user-authored messages.
        """
        self.session_id = session_id
        self.owner_id = owner_id
        self.messages: List[Dict[str, Any]] = messages if messages is not None else []
        self.meta: Dict[str, Any] = meta or {}
        self.guard_user_message_loss = guard_user_message_loss
        self._renamed = False

    # Queries.

    def message_id(self, message: Dict[str, Any]) -> Optional[str]:
        """Return a message's stable id, if any."""
        value = message.get(self.external_id_field)
        return str(value) if value else None

    def index_of(self, message_id: str) -> int:
        """Return a message index or raise ``MessageNotFound``."""
        for i, m in enumerate(self.messages):
            if self.message_id(m) == message_id:
                return i
        raise MessageNotFound(f"message {message_id!r} not in session {self.session_id!r}")

    @property
    def last(self) -> Optional[Dict[str, Any]]:
        return self.messages[-1] if self.messages else None

    @property
    def ends_with_open_reply(self) -> bool:
        """Whether history ends in a continuation-eligible assistant reply."""
        i = len(self.messages) - 1
        while i >= 0 and self.is_companion(self.messages[i]):
            i -= 1
        m = self.messages[i] if i >= 0 else None
        return bool(
            isinstance(m, dict)
            and m.get("role") == "assistant"
            and not m.get("tool_calls")
        )

    # History operations addressed by message id.

    def is_companion(self, message: Dict[str, Any]) -> bool:
        """Whether a message is metadata attached to a preceding user turn."""
        return message.get("role") == "user" and bool(message.get(self.system_marker_field))

    def user_turns(self) -> List[int]:
        """Return indexes of independent user-authored turns."""
        return [i for i, m in enumerate(self.messages) if self.is_user_turn(m)]

    def is_user_turn(self, message: Dict[str, Any]) -> bool:
        """Whether a message is an independent user-authored contribution."""
        return message.get("role") == "user" and not message.get(self.system_marker_field)

    def _truncate(self, idx: int, message_id: str, consent: bool) -> int:
        """Apply a truncation after checking the configured consent guard."""
        doomed = self.messages[idx:]
        user_loss = sum(1 for m in doomed if self.is_user_turn(m))
        if user_loss and not consent and self.guard_user_message_loss:
            raise TruncationNeedsConsent(
                message_id=message_id,
                dropped=len(doomed),
                dropped_user_messages=user_loss,
                total=len(self.messages),
            )
        del self.messages[idx:]
        return len(doomed)

    def truncate_after(self, message_id: str, *, consent: bool = False) -> int:
        """Discard history after a message while retaining its companions."""
        idx = self.index_of(message_id)
        while idx + 1 < len(self.messages) and self.is_companion(self.messages[idx + 1]):
            idx += 1
        return self._truncate(idx + 1, message_id, consent)

    def truncate_before(self, message_id: str, *, consent: bool = False) -> int:
        """Discard a message and everything after it, subject to consent."""
        return self._truncate(self.index_of(message_id), message_id, consent)

    # Title.
    #
    # Title derivation belongs to the session; products translate it into UI.

    @property
    def title(self) -> str:
        return str((self.meta or {}).get(self.title_field) or "")

    @title.setter
    def title(self, value: str) -> None:
        if self.meta is None:  # pyright: ignore[reportUnnecessaryComparison]
            self.meta = {}
        self.meta[self.title_field] = value
        self._renamed = True

    @property
    def pending_interaction(self) -> Optional[Dict[str, Any]]:
        """Return the persisted pending interaction, if any."""
        raw: Any = (self.meta or {}).get(self.suspended_field)
        if isinstance(raw, dict) and raw:
            return cast(Dict[str, Any], raw)
        return None

    def clear_pending_interaction(self) -> None:
        if self.meta:
            self.meta.pop(self.suspended_field, None)

    def truncate_tool_calls_after(self, tool_call_id: str) -> Optional[int]:
        """Drop unexecuted calls after a suspended call and return its message index."""
        for i in range(len(self.messages) - 1, -1, -1):
            msg = self.messages[i]
            if msg.get("role") != "assistant" or not isinstance(msg.get("tool_calls"), list):
                continue
            calls = cast(List[Any], msg["tool_calls"])
            for k, tc in enumerate(calls):
                if isinstance(tc, dict) and str(cast(Dict[str, Any], tc).get("id") or "") == tool_call_id:
                    if k + 1 < len(calls):
                        msg["tool_calls"] = calls[: k + 1]
                        return i
                    return None
        return None

    def has_tool_call(self, tool_call_id: str) -> bool:
        for msg in self.messages:
            if msg.get("role") == "assistant" and isinstance(msg.get("tool_calls"), list):
                for tc in cast(List[Any], msg["tool_calls"]):
                    if isinstance(tc, dict) and str(cast(Dict[str, Any], tc).get("id") or "") == tool_call_id:
                        return True
        return False

    def has_tool_result(self, tool_call_id: str) -> bool:
        return any(
            msg.get("role") == "tool" and str(msg.get("tool_call_id") or "") == tool_call_id
            for msg in self.messages
        )

    @property
    def opened_packages(self) -> List[str]:
        """Return valid opened package paths in stored order."""
        raw: Any = (self.meta or {}).get(self.opened_packages_field)
        if not isinstance(raw, list):
            return []
        out: List[str] = []
        for p in cast(List[Any], raw):
            ps = str(p or "").strip() if isinstance(p, str) else ""
            if ps and ps.startswith("/tools/") and ps != "/tools":
                out.append(ps)
        return out

    @property
    def overlay_packages(self) -> List[str]:
        """Return valid product-provided package overlays."""
        raw: Any = (self.meta or {}).get(self.overlay_packages_field)
        if not isinstance(raw, list):
            return []
        out: List[str] = []
        for p in cast(List[Any], raw):
            ps = str(p or "").strip() if isinstance(p, str) else ""
            if ps and ps.startswith("/tools/") and ps != "/tools":
                out.append(ps)
        return out

    @property
    def effective_packages(self) -> List[str]:
        """Return opened and overlay packages, deduplicated in order."""
        out: List[str] = []
        for p in self.opened_packages + self.overlay_packages:
            if p not in out:
                out.append(p)
        return out

    def set_opened_packages(self, paths: List[str]) -> None:
        if self.meta is None:  # pyright: ignore[reportUnnecessaryComparison]
            self.meta = {}
        self.meta[self.opened_packages_field] = list(paths)

    @classmethod
    def opened_packages_from_history(cls, messages: List[Dict[str, Any]]) -> List[str]:
        """Replay product-defined package actions to reconstruct package state."""
        open_action = cls.open_package_action_name
        close_action = cls.close_package_action_name
        if not open_action or not close_action:
            return []
        opened: set[str] = set()
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for tc_raw in cast(List[Any], msg.get("tool_calls") or []):
                if not isinstance(tc_raw, dict):
                    continue
                tc = cast(Dict[str, Any], tc_raw)
                func = cast(Dict[str, Any], tc.get("function") or {})
                name = str(func.get("name") or "")
                if name not in (open_action, close_action):
                    continue
                args_raw: Any = func.get("arguments")
                args: Dict[str, Any]
                if isinstance(args_raw, dict):
                    args = cast(Dict[str, Any], args_raw)
                else:
                    try:
                        parsed: Any = json.loads(args_raw) if args_raw else {}
                    except Exception:
                        parsed = {}
                    args = cast(Dict[str, Any], parsed) if isinstance(parsed, dict) else {}
                paths_raw: Any = args.get("package_paths")
                if isinstance(paths_raw, str):
                    paths_raw = [paths_raw]
                if not isinstance(paths_raw, list):
                    continue
                for p in cast(List[Any], paths_raw):
                    ps = p.strip() if isinstance(p, str) else ""
                    if not ps or ps == "/tools":
                        continue
                    if name == open_action:
                        if ps.startswith("/tools/"):
                            opened.add(ps)
                    else:
                        opened.discard(ps)
        return sorted(opened)

    @property
    def renamed(self) -> bool:
        """Whether this session was renamed during the current turn."""
        return self._renamed

    def take_renamed(self) -> Optional[str]:
        """Consume and return a newly derived title, if one is pending."""
        if not self._renamed:
            return None
        self._renamed = False
        return self.title

    def derive_title(self, message: Dict[str, Any]) -> str:
        """Derive a title from one user message; products may override it."""
        content = message.get("content")
        return content[:50] if isinstance(content, str) else ""

    def rewind_to_user_turn(self, index: int, *, consent: bool = False) -> int:
        """Retain a selected user turn and its companions; truncate later history."""
        turns = self.user_turns()
        if not turns or not (-len(turns) <= index < len(turns)):
            raise MessageNotFound(f"user turn #{index} not found (have {len(turns)})")
        keep = turns[index]
        while keep + 1 < len(self.messages) and self.is_companion(self.messages[keep + 1]):
            keep += 1
        anchor_id = self.message_id(self.messages[turns[index]]) or f"user_turn[{index}]"
        return self._truncate(keep + 1, anchor_id, consent)

    def append(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Append a message and derive a title from the first user turn."""
        self.messages.append(message)
        if self.is_user_turn(message) and sum(
            1 for m in self.messages if self.is_user_turn(m)
        ) == 1:
            derived = self.derive_title(message)
            if derived:
                self.title = derived
        return message

    def __len__(self) -> int:
        return len(self.messages)

    @override
    def __repr__(self) -> str:
        tail = " open_reply" if self.ends_with_open_reply else ""
        return (f"<Session {self.session_id} owner={self.owner_id!r} "
                f"messages={len(self.messages)}{tail}>")


__all__ = ["Session", "MessageNotFound", "TruncationNeedsConsent"]
