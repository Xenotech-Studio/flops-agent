"""Normalize tool shapes: callables to OpenAI schemas; calls and results to history values."""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple, cast


def normalize_tools(tools: List[Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Callable[..., Any]]]:
    """Split ``tools`` into (LLM schemas, name→callable map).

    Each item may be a bare callable (schema derived from its signature) or a
    dict: a raw OpenAI tool schema, or ``{"schema": <schema>, "fn": <callable>}``.
    """
    schemas: List[Dict[str, Any]] = []
    fn_map: Dict[str, Callable[..., Any]] = {}
    for t in tools:
        if callable(t):
            name = getattr(t, "__name__", None) or "tool"
            schemas.append(schema_from_callable(t, name))
            fn_map[name] = t
        elif isinstance(t, dict):
            t_dict = cast(Dict[str, Any], t)
            schema = cast(Dict[str, Any], t_dict.get("schema") or t_dict)
            schemas.append(schema)
            fn = t_dict.get("fn")
            name = (cast(Dict[str, Any], schema.get("function") or {}).get("name")
                    if isinstance(schema, dict) else None) or getattr(fn, "__name__", None)  # pyright: ignore[reportUnnecessaryIsInstance] — a cast is only an assertion; schema fields may not be dicts
            if fn is not None and name:
                fn_map[name] = fn
    return schemas, fn_map


def schema_from_callable(fn: Callable[..., Any], name: str) -> Dict[str, Any]:
    """Minimal OpenAI tool schema from a callable's signature (params as strings)."""
    props: Dict[str, Any] = {}
    required: List[str] = []
    try:
        sig = inspect.signature(fn)
        for pname, p in sig.parameters.items():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            props[pname] = {"type": "string"}
            if p.default is inspect.Parameter.empty:
                required.append(pname)
    except (ValueError, TypeError):
        pass
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": (inspect.getdoc(fn) or "").strip(),
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def tool_call_from_dict(d: Dict[str, Any]) -> Any:
    """Convert an OpenAI tool_call history dict into the loop's call object.

    This is the inverse of ``tool_call_to_openai``: on resumed dispatch, it makes the final
    assistant message's tool_calls dispatchable again.
    """
    fn_raw = d.get("function")
    fn: Dict[str, Any] = cast(Dict[str, Any], fn_raw) if isinstance(fn_raw, dict) else {}
    args = fn.get("arguments")
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False) if args else "{}"
    return SimpleNamespace(
        id=d.get("id"),
        type=d.get("type") or "function",
        function=SimpleNamespace(name=str(fn.get("name") or ""), arguments=args),
    )


def tool_call_to_openai(tc: Any) -> Dict[str, Any]:
    """Convert a call accumulated from the stream into an OpenAI tool_call history dict."""
    return {
        "id": getattr(tc, "id", None),
        "type": "function",
        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
    }


def result_to_content(result: Any) -> str:
    """Convert a tool return value to message content (strings unchanged; everything else JSON)."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)




# ── Tool arguments: parsing and repair ───────────────────────────────────────
#
# Model-generated ``function.arguments`` JSON is **not guaranteed valid**. Treating a bare
# ``json.loads`` failure as empty arguments conflates a truncated stream with invalid syntax.
# The former should retry; the latter should return the original text to the model for repair.
# The parser therefore reports why it failed as well.

#: Failure classifications from :func:`parse_tool_arguments`.
FAIL_NOT_OBJECT = "not_object"    # Valid JSON, but not an object (the model sent an array or scalar).
FAIL_INVALID_JSON = "invalid_json"  # Balanced delimiters but invalid syntax: bad content, not transport damage.
FAIL_TRUNCATED = "truncated"      # Unbalanced delimiters: incomplete tail, typically a stream interruption or idle timeout.


@dataclass
class ParsedArguments:
    """Result of a tool-argument parse. ``arguments`` is empty when ``ok`` is false."""

    arguments: Dict[str, Any]
    ok: bool = True
    #: Failure classification (one of ``FAIL_*``); empty on success.
    fail_kind: str = ""
    #: The parser's original error text, for logs and feedback to the model; empty on success.
    error: str = ""
    #: Whether :func:`repair_unquoted_json_values` recovered invalid original input.
    repaired: bool = False


def parse_tool_arguments(raw: Any) -> ParsedArguments:
    """Convert ``function.arguments`` to an argument dict, with a failure classification.

    Accept dictionaries unchanged (some providers supply objects directly). Parse strings,
    then try an unquoted-bare-value repair. If that fails, delimiter balance distinguishes
    ``truncated`` from ``invalid_json``. Empty strings and None are successful empty arguments.
    """
    if isinstance(raw, dict):
        return ParsedArguments(dict(cast(Dict[str, Any], raw)))
    if raw is None:
        return ParsedArguments({})
    if not isinstance(raw, str):
        return ParsedArguments({}, ok=False, fail_kind=FAIL_NOT_OBJECT, error=f"arguments is {type(raw).__name__}")
    text = raw.strip()
    if not text:
        return ParsedArguments({})
    try:
        obj: Any = json.loads(text)
    except Exception as exc:
        repaired = repair_unquoted_json_values(text)
        if repaired is not None:
            try:
                fixed: Any = json.loads(repaired)
            except Exception:
                fixed = None
            if isinstance(fixed, dict):
                return ParsedArguments(cast(Dict[str, Any], fixed), error=str(exc), repaired=True)
        kind = FAIL_INVALID_JSON if json_brackets_balanced(text) else FAIL_TRUNCATED
        return ParsedArguments({}, ok=False, fail_kind=kind, error=str(exc))
    if not isinstance(obj, dict):
        return ParsedArguments({}, ok=False, fail_kind=FAIL_NOT_OBJECT, error="arguments is not a JSON object")
    return ParsedArguments(cast(Dict[str, Any], obj))


def json_brackets_balanced(s: str) -> bool:
    """Roughly determine whether ``{}`` / ``[]`` delimiters balance, ignoring strings.

    Balanced delimiters usually mean invalid syntax rather than a transport truncation;
    unbalanced ones mean an incomplete tail, usually from an interrupted stream or idle timeout.
    """
    depth = 0
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_str


def repair_unquoted_json_values(s: str) -> Optional[str]:
    """Best-effort repair by quoting bare string values after object colons.

    A typical malformed form (occasionally produced with CJK and full-width punctuation)::

        {"a": ["x"], "search_goal": research…information, "search_mode": "y"}

    ``search_goal`` has an unquoted value. After an object-level ``:``, if the next nonspace
    character cannot start valid JSON, read through the next same-level delimiter, trim and quote
    the value (escaping internal quotes and backslashes). Return it only if it parses as an object.
    """
    if not s or ":" not in s:
        return None
    out: List[str] = []
    i = 0
    n = len(s)
    in_str = False
    esc = False
    repaired = False
    value_start = set('"{[-0123456789tfn')
    while i < n:
        ch = s[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch != ":":
            out.append(ch)
            i += 1
            continue
        # Object-level colon: skip whitespace and inspect the value's first character.
        out.append(ch)
        i += 1
        j = i
        while j < n and s[j] in " \t\r\n":
            out.append(s[j])
            j += 1
        if j >= n:
            i = j
            break
        if s[j] in value_start:
            i = j                       # Valid value start; leave it for the next scan iteration.
            continue
        # Bare value: read to the same-level , / } / ] (it is unquoted, so string state is irrelevant).
        k = j
        while k < n and s[k] not in ",}]":
            k += 1
        bare = s[j:k].rstrip()
        trailing_ws = s[j + len(bare):k]        # Restore trailing whitespace removed by rstrip.
        escaped = bare.replace("\\", "\\\\").replace('"', '\\"')
        # Escape possible raw control characters so the repaired JSON is valid.
        escaped = escaped.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
        out.append('"' + escaped + '"' + trailing_ws)
        repaired = True
        i = k
    if not repaired:
        return None
    candidate = "".join(out)
    try:
        obj: Any = json.loads(candidate)
    except Exception:
        return None
    return candidate if isinstance(obj, dict) else None


__all__ = [
    "normalize_tools", "schema_from_callable", "tool_call_from_dict", "tool_call_to_openai",
    "result_to_content", "ParsedArguments", "parse_tool_arguments", "json_brackets_balanced",
    "repair_unquoted_json_values", "FAIL_NOT_OBJECT", "FAIL_INVALID_JSON", "FAIL_TRUNCATED",
]
