"""Standalone test configuration.

The suite deliberately does not add an embedding application's source directory
to ``sys.path``. Install this checkout first (for example,
``pip install -e .[test,providers]``) so tests exercise the distributable
package boundary.
"""

import flops_agent


def pytest_sessionstart() -> None:
    """Fail early if the independently installed package cannot be imported."""
    assert flops_agent.__version__


# ── Stream-chunk builders shared by the suite ────────────────────────────────
#
# The LLM seam speaks ``StreamChunk`` (text / reasoning / tool_call / finish),
# so a test double builds those four directly -- there is no provider wire
# format to imitate. Keep every scripted stream in the suite on these helpers
# rather than hand-rolling chunks per file.

import json
from typing import Any, Optional, Union

from flops_agent import (
    FinishStreamChunk,
    ReasoningStreamChunk,
    TextStreamChunk,
    ToolCallStreamChunk,
)


def text_chunk(text: str) -> TextStreamChunk:
    """One visible-text delta."""
    return TextStreamChunk(text)


def reasoning_chunk(text: str) -> ReasoningStreamChunk:
    """One reasoning-channel delta."""
    return ReasoningStreamChunk(text)


def tool_chunk(
    name: Optional[str] = None,
    args: Union[str, dict, None] = "{}",
    *,
    index: int = 0,
    cid: Optional[str] = "c1",
) -> ToolCallStreamChunk:
    """One tool-call fragment; ``args`` may be a JSON string or a dict.

    Providers may split a call across several fragments (name first, then
    argument pieces). A scripted turn usually has no reason to: passing name
    and arguments together is a legal single fragment.
    """
    return ToolCallStreamChunk(
        index=index,
        id=cid,
        name=name,
        arguments_delta=args if isinstance(args, str) else json.dumps(args),
    )


def finish_chunk(reason: Optional[str] = None, usage: Optional[Any] = None) -> FinishStreamChunk:
    """The terminal frame carrying ``finish_reason`` and complete usage."""
    return FinishStreamChunk(reason=reason, usage=usage)
