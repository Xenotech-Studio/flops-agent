"""Built-in ``ask_user_question`` tool.

The model supplies questions and options, this tool normalizes them into an
``InteractionRequest``, and the framework suspends the turn. A later answer
query resumes the model with the answers as this tool's result.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

from ..engine.interaction import InteractionRequest
from .registry import DEFAULT_REGISTRY, ToolContext, ToolRegistry

ASK_USER_QUESTION_DEF: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ask_user_question",
        "description": "Ask one or more questions with explicit choices. The turn pauses for the user's selection, which returns as answers before execution continues.",
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "One to four questions. Each has question, header, multiSelect, and two to four {label, description} options.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "header": {"type": "string"},
                            "multiSelect": {"type": "boolean"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {"label": {"type": "string"}, "description": {"type": "string"}},
                                    "required": ["label"],
                                },
                            },
                        },
                        "required": ["question", "options"],
                    },
                }
            },
            "required": ["questions"],
        },
    },
}

MAX_QUESTIONS = 4
MAX_OPTIONS = 6


def normalize_questions(arguments: Any) -> List[Dict[str, Any]]:
    """Normalize model questions (at most four questions and six options each)."""
    out: List[Dict[str, Any]] = []
    raw = cast(Dict[str, Any], arguments).get("questions") if isinstance(arguments, dict) else None
    if not isinstance(raw, list):
        return out
    for q_raw in cast(List[Any], raw)[:MAX_QUESTIONS]:
        if not isinstance(q_raw, dict):
            continue
        q = cast(Dict[str, Any], q_raw)
        qtext = str(q.get("question") or "").strip()
        if not qtext:
            continue
        opts: List[Dict[str, str]] = []
        for o in cast(List[Any], q.get("options") or [])[:MAX_OPTIONS]:
            if isinstance(o, dict):
                od = cast(Dict[str, Any], o)
                if str(od.get("label") or "").strip():
                    opts.append({"label": str(od["label"]).strip(), "description": str(od.get("description") or "").strip()})
            elif isinstance(o, str) and o.strip():
                opts.append({"label": o.strip(), "description": ""})
        out.append({
            "question": qtext,
            "header": str(q.get("header") or "").strip()[:16],
            "multiSelect": bool(q.get("multiSelect")),
            "options": opts,
        })
    return out


async def _ask(arguments: Dict[str, Any], ctx: ToolContext) -> Any:
    qs = normalize_questions(arguments)
    if not qs:
        return {
            "success": False,
            "error": "ask_user_question requires questions:[{question, options:[{label, description}], multiSelect}] (one to four questions, each with two to four options)",
        }
    return InteractionRequest(kind="ask_user_question", payload={"questions": qs})


def register_ask_user_question(registry: Optional[ToolRegistry] = None, *, tool_def: Optional[Dict[str, Any]] = None) -> None:
    """Register ask_user_question at ``/tools``; ``tool_def`` may supply product copy."""
    reg = registry if registry is not None else DEFAULT_REGISTRY
    reg.register_tool("/tools", "ask_user_question", tool_def or ASK_USER_QUESTION_DEF)
    reg.register_unified_handler("/tools", "ask_user_question", _ask)


__all__ = ["ASK_USER_QUESTION_DEF", "normalize_questions", "register_ask_user_question"]
