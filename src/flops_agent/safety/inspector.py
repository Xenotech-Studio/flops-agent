"""Command safety review for executable work on a user's machine.

The framework supplies the process and products supply the policy: rules flag
commands, sandbox policy may approve them, and an inspector can allow, request
confirmation, withdraw, or reject. If inspection is unavailable, the framework
always requests confirmation rather than allowing execution. This conservative
failure mode is fixed in :func:`fallback_decision`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, cast, runtime_checkable


def _empty_matched() -> List[Dict[str, Any]]:
    return []


class Verdict(str, Enum):
    """Review outcome."""

    ALLOW = "allow"
    """Allow execution."""

    NEED_CONFIRM = "need_confirm_after_warning"
    """Require user confirmation; this is the conservative fallback."""

    WITHDRAW = "withdraw_and_rethink"
    """Ask the agent to take another approach without involving the user."""

    DANGEROUS = "dangerous_without_confirm"
    """Reject outright without asking the user."""

    @classmethod
    def parse(cls, raw: Any) -> "Verdict":
        """Parse an inspector value; unrecognized values require confirmation."""
        try:
            return cls(str(raw or "").strip())
        except ValueError:
            return cls.NEED_CONFIRM


@dataclass
class Rule:
    """One dangerous-command rule."""

    id: str
    pattern: str
    label: str
    """User-facing explanation, for example that a command deletes files."""
    risk: str = "medium"
    """``high`` or ``medium``; affects wording, not workflow."""


@dataclass
class Review:
    """Review result."""

    verdict: Verdict
    reason: str = ""
    """User-facing rationale."""
    advice: str = ""
    """Safer alternative for the agent."""
    matched: List[Dict[str, Any]] = field(default_factory=_empty_matched)
    command: str = ""
    raw: Optional[Dict[str, Any]] = None
    """Raw inspector output for diagnosis."""

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW

    @property
    def needs_user(self) -> bool:
        """Whether the user must be involved; ``WITHDRAW`` does not require it."""
        return self.verdict is Verdict.NEED_CONFIRM


@runtime_checkable
class Inspector(Protocol):
    """Inspector protocol; implementations define their own judgment standard."""

    def review(
        self,
        command: str,
        *,
        cwd: str = "",
        matched: Optional[List[Dict[str, Any]]] = None,
        context: Any = None,
    ) -> Review:
        ...


def scan(command: str, rules: List[Any]) -> List[Dict[str, Any]]:
    """Scan a command against rules and return matches, tolerating bad regexes."""
    import re

    matched: List[Dict[str, Any]] = []
    text = (command or "").strip()
    if not text:
        return matched
    for rule in rules or []:
        if isinstance(rule, dict):
            rule_dict = cast(Dict[str, Any], rule)
            pattern = rule_dict.get("pattern")
        else:
            pattern = getattr(rule, "pattern", "")
        if not pattern:
            continue
        try:
            if not re.search(str(pattern), text, flags=re.IGNORECASE):
                continue
        except re.error:
            continue                      # Skip a bad regex without disabling other rules.
        if isinstance(rule, dict):
            rule_dict = cast(Dict[str, Any], rule)
            matched.append({
                "id": str(rule_dict.get("id") or ""),
                "label": str(rule_dict.get("label") or ""),
                "risk": str(rule_dict.get("risk") or "medium"),
                "pattern": str(pattern),
            })
        else:
            matched.append({
                "id": rule.id, "label": rule.label,
                "risk": rule.risk, "pattern": rule.pattern,
            })
    return matched


def fallback_decision(command: str, matched: List[Dict[str, Any]], reason: str) -> Review:
    """Return the conservative outcome when inspection is unavailable."""
    return Review(
        verdict=Verdict.NEED_CONFIRM,
        reason=reason or "Safety review is unavailable; confirmation is required as a precaution.",
        advice="Confirm the command's scope and impact before deciding whether to execute it.",
        matched=list(matched or []),
        command=command,
    )


__all__ = ["Verdict", "Rule", "Review", "Inspector", "scan", "fallback_decision"]
