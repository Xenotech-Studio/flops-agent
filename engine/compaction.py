"""Read-time projection/trimming of conversation history + compaction (summarization) mechanism.

Relationship to :meth:`Runner.project_messages`: this module is the framework's default
answer to **what mechanism** that override point should use. The two concerns are
understood and implemented separately:

- **Read-time projection** (:class:`ProjectionConfig` methods + :func:`expand_canonical_to_llm_messages` /
  :func:`build_llm_messages_with_context_projection`): pure functions, no LLM calls, no side
  effects. Recomputed from scratch before every request, folding the canonical history into
  "the wire payload we're about to send" — tool results are graded and trimmed by recency,
  user text / tool_call arguments are soft-trimmed, only the most recent images are kept, and
  if a compaction summary is currently active, the summary plus a verbatim tail are expanded.
- **Actually summarizing history** (:class:`CompactionPolicy`'s :meth:`~CompactionPolicy.plan` /
  the quality gate / :func:`default_summarize`): calls an LLM, produces a new
  :class:`~flops_agent.seams.compaction_store.CompactionRecord`, and persists it (via
  :class:`~flops_agent.seams.compaction_store.CompactionStore`). This does NOT happen on every
  request — it fires only when a threshold is crossed.

Both share one set of char/token measurement and grading primitives (:class:`ProjectionConfig`).
The compaction judgment ("how big would the summary-injection + tail be right now") and the
read-time projection ("how much does the tail actually need to be trimmed to") must use the
exact same ruler — otherwise the judgment says "compaction needed" while the actual wire payload
built afterward isn't that big (or vice versa), producing oscillation or even deadlock.

Products may override the constructor parameters of ``ProjectionConfig`` / ``CompactionPolicy``
(or replace the algorithm entirely), but in most cases the framework defaults are already
production-validated practice — usually you only need to fill in your own threshold numbers
(config values) at construction time.

Things the product still has to supply itself (these are inherently product infrastructure,
not framework mechanism):
- Resolving the model's context window (multi-tier priority: ops-configured parameter table →
  resource-node reported value → curated authoritative table → provider-reported value →
  hardcoded fallback) — functions in this module only accept an already-resolved
  ``window_usable: int`` (0 = unavailable).
- The LLM routing that actually issues the summarization call (multi-provider / BYOK / proxy
  env) — see the ``complete`` callback parameter of :func:`default_summarize`; the framework
  only handles "build the prompt, parse the result, run the quality gate" — how the model is
  actually invoked is up to the product.
- Persistence of compaction records — see :class:`~flops_agent.seams.compaction_store.CompactionStore`.
- Product-specific sanitization of tool content (e.g. stripping out widget code meant only for
  frontend rendering) — injected via the ``ProjectionConfig.sanitize_tool_content`` /
  ``sanitize_tool_text`` hooks, which default to identity.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple, cast

from flops_agent.seams.compaction_store import CompactionRecord

logger = logging.getLogger(__name__)

FormatMetadata = Callable[[Dict[str, Any]], str]

# ---------------------------------------------------------------------------
# Content-parsing primitives (config-free)
# ---------------------------------------------------------------------------


def is_image_block(b: Any) -> bool:
    if not isinstance(b, dict):
        return False
    b = cast(Dict[str, Any], b)
    return b.get("type") in ("image", "image_url") or "image_url" in b


def content_text_and_image_count(content: Any) -> Tuple[str, int]:
    """Split user/assistant content into (concatenated text, image block count)."""
    if content is None:
        return "", 0
    if isinstance(content, str):
        return content, 0
    if isinstance(content, list):
        content = cast(List[Any], content)
        parts: List[str] = []
        imgs = 0
        for b in content:
            if isinstance(b, dict):
                b = cast(Dict[str, Any], b)
                if is_image_block(b):
                    imgs += 1
                else:
                    t = b.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts), imgs
    return str(content), 0


def text_from_content(content: Any, max_chars: int = 8000) -> str:
    """Flatten content (str or block list) into plain text, for summary transcript serialization."""
    if content is None:
        return ""
    if isinstance(content, str):
        s = content
    elif isinstance(content, list):
        content = cast(List[Any], content)
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block = cast(Dict[str, Any], block)
            if block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        s = "\n".join(parts)
    else:
        s = str(content)
    if len(s) > max_chars:
        return s[:max_chars] + f"\n...[truncated {len(s) - max_chars} chars]"
    return s


_MIDDLE_OMIT_MARKER = "\n...[middle omitted {n} chars · full content retained server-side]...\n"


def middle_omit_trim(s: str, max_chars: int) -> str:
    """Generic "keep head and tail, omit the middle" soft-trim — returns unchanged if not over
    length. The stored original is never mutated; trimming only ever happens in the projection."""
    if not s or max_chars <= 0 or len(s) <= max_chars:
        return s
    marker_room = len(_MIDDLE_OMIT_MARKER.format(n=len(s))) + 8
    budget = max_chars - marker_room
    if budget < 64:
        return s[:max_chars] + f"\n...[truncated {len(s) - max_chars} chars · full content retained server-side]"
    h = budget // 2
    t = budget - h
    omitted = len(s) - h - t
    return s[:h] + _MIDDLE_OMIT_MARKER.format(n=omitted) + s[len(s) - t:]


def adjust_tail_start_for_tool_protocol(messages: List[Dict[str, Any]], start_idx: int) -> int:
    """When the compaction/trim split point lands in the middle of a "tool turn", the tail would
    start with role=tool; OpenAI/Dashscope and similar APIs require a tool message to
    immediately follow the assistant message that issued its tool_calls. If the tail is
    detected to start with tool, align it backward to that assistant turn; otherwise drop the
    orphaned leading tool blocks."""
    n = len(messages)
    if start_idx <= 0 or start_idx >= n:
        return start_idx
    if messages[start_idx].get("role") != "tool":
        return start_idx
    j = start_idx - 1
    while j >= 0 and messages[j].get("role") == "tool":
        j -= 1
    if j >= 0 and messages[j].get("role") == "assistant" and messages[j].get("tool_calls"):
        return j
    k = start_idx
    while k < n and messages[k].get("role") == "tool":
        k += 1
    return k


# ---------------------------------------------------------------------------
# Token measurement: CJK-aware estimation (naive len/4 undercounts Chinese text by
# almost 2x, so CJK characters must be split out and estimated separately)
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(
    "[　-〿぀-ヿ㐀-䶿一-鿿"
    "豈-﫿＀-￯가-힯]"
)


def _identity(x: Any) -> Any:
    return x


_TOOL_STUB_TEMPLATE = (
    "[old tool result elided · ~{n} chars · re-run the tool to retrieve the full output]"
)
_IMAGE_STUB_TEXT = (
    "[old image elided to save context · only the most recent images are kept inline]"
)
_METADATA_LINE_PREFIX = "Message metadata (not user speech): "


@dataclass
class ProjectionConfig:
    """Measurement/trimming primitives shared by read-time projection and compaction
    judgment, all configurable; defaults are production-validated practice.

    One ruler used in two places: the compaction judgment's "how big is the total right
    now" and the read-time projection's "how should the tail actually be trimmed" must use
    the same units. Otherwise the two ends compute independently and you get oscillation or
    deadlock ("judgment says compact, but the actual wire payload isn't that big"). All
    weight calculations should go through this object's methods — don't invent a second
    estimation path.
    """

    # Token estimation (tokens ≈ ceil((CJK chars / cjk_ratio + other chars / latin_ratio) × safety margin × global calibration))
    cjk_chars_per_token: float = 1.7
    latin_chars_per_token: float = 4.0
    image_tokens: int = 1500
    safety_margin: float = 1.15
    global_calibration: float = 1.0

    # Standard-tier soft-trim for tool results (keep head and tail, omit the middle)
    tool_soft_trim_enabled: bool = True
    tool_soft_trim_max_chars: int = 4000
    tool_soft_trim_head_chars: int = 1500
    tool_soft_trim_tail_chars: int = 1500
    #: Tool results whose name is in this exempt set are never soft-trimmed — the classic
    #: case being "read a document, then edit by locating against the original text" tools:
    #: once the middle is omitted, the model can only guess at it from head/tail, and any
    #: anchor text it produces won't match the actual content.
    tool_soft_trim_exempt_names: FrozenSet[str] = frozenset()

    # Recency grading for tool results (read-time-projection-specific; the compaction
    # judgment's tail token weighing also reuses this same grading table)
    proj_recency_grading_enabled: bool = True
    proj_recent_tool_full_count: int = 3
    proj_recent_tool_full_max_chars: int = 16_000
    proj_tool_stub_after_count: int = 24
    proj_tool_stub_min_chars: int = 400
    #: Number of most-recent images kept live in the projection; older ones are replaced
    #: with a text placeholder. Negative = disabled.
    proj_recent_image_keep: int = 2

    # Soft-trim for user text / tool_call arguments
    user_msg_soft_trim_enabled: bool = True
    user_msg_soft_trim_max_chars: int = 600
    #: Regime B: the character floor that the latest user message is always allowed to keep,
    #: no matter how tight the remaining budget is.
    user_latest_msg_min_chars: int = 4_000
    toolcall_args_soft_trim_enabled: bool = True
    toolcall_args_soft_trim_max_chars: int = 4_000

    #: Product-specific sanitization hook for tool content (e.g. stripping out widget code
    #: meant only for frontend rendering). Must run before soft-trimming — once content has
    #: been cut down to a half-string, its structured form can no longer be parsed out for
    #: sanitization. Defaults to identity.
    sanitize_tool_content: Callable[[Any], Any] = _identity
    sanitize_tool_text: Callable[[str], str] = _identity

    # ── Token estimation ──────────────────────────────────────────────

    def estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        n = len(text)
        other = len(_CJK_RE.sub("", text))
        cjk = n - other
        raw = cjk / self.cjk_chars_per_token + other / self.latin_chars_per_token
        return int(math.ceil(raw * self.safety_margin * self.global_calibration))

    def weigh_text(self, s: str, unit: str) -> int:
        """unit='token' → estimate_tokens; otherwise the character count."""
        if not s:
            return 0
        return self.estimate_tokens(s) if unit == "token" else len(s)

    def image_weight(self, img_count: int, unit: str) -> int:
        """Image block weight: in token units, a flat per-image constant; in char units,
        stays 0 (images don't count against the char budget)."""
        if unit != "token" or img_count <= 0:
            return 0
        return img_count * self.image_tokens

    def approx_tokens_from_chars(self, chars: int) -> int:
        """**Fallback** path: when only a character count is available (no original text),
        roughly convert a fixed overhead into tokens (pure Latin conversion, which
        undercounts CJK-heavy text). Only use this when :meth:`estimate_tokens` can't be
        run against the original text."""
        c = max(0, int(chars))
        if c == 0:
            return 0
        return int(math.ceil(c / self.latin_chars_per_token * self.safety_margin * self.global_calibration))

    # ── Tool content: sanitize → soft-trim ────────────────────────────

    def tool_content_to_raw_str(self, content: Any) -> str:
        """Normalize a tool message's content into a string (sanitized, not yet trimmed)."""
        if content is None:
            return ""
        if isinstance(content, str):
            return self.sanitize_tool_text(content)
        try:
            return json.dumps(self.sanitize_tool_content(content), ensure_ascii=False)
        except (TypeError, ValueError):
            return str(content)

    def soft_trim_tool_text(
        self, s: str, *, tool_name: Optional[str] = None, max_chars_override: Optional[int] = None
    ) -> str:
        """When over budget, keep a head segment and a tail segment and omit the middle;
        exempt tools or a disabled flag returns the text unchanged.

        ``max_chars_override``: used by recency grading — the "most recent N" tier gets a
        larger budget, with head/tail sized adaptively to that budget (rather than the fixed
        head/tail config), keeping as much of the near-end tool output as possible.
        """
        if tool_name and tool_name in self.tool_soft_trim_exempt_names:
            return s or ""
        if not self.tool_soft_trim_enabled or not s:
            return s
        if max_chars_override is not None and max_chars_override > 0:
            max_c = max_chars_override
            head_n = tail_n = max_c
        else:
            max_c = self.tool_soft_trim_max_chars
            head_n = self.tool_soft_trim_head_chars
            tail_n = self.tool_soft_trim_tail_chars
        if len(s) <= max_c:
            return s
        marker = "\n...[middle omitted {n} chars — re-run the tool for the full result]...\n"
        marker_room = len(marker.format(n=0)) + 32
        budget = max_c - marker_room
        if budget < 64:
            return s[:max_c] + f"\n...[truncated {len(s) - max_c} chars — re-run the tool for full output]"
        h = min(head_n, budget // 2)
        t = min(tail_n, budget - h)
        if h + t == 0 or h + t >= len(s):
            return s[:max_c] + f"\n...[truncated {len(s) - max_c} chars — re-run the tool for full output]"
        omitted = len(s) - h - t
        mid = marker.format(n=omitted)
        return s[:h] + mid + s[len(s) - t:]

    def tool_content_to_trimmed_str(self, content: Any, *, tool_name: Optional[str] = None) -> str:
        return self.soft_trim_tool_text(self.tool_content_to_raw_str(content), tool_name=tool_name)

    # ── Recency grading for tool results (shared table for read-time projection + compaction judgment) ──

    def compute_tool_tiers(self, messages: List[Dict[str, Any]]) -> Dict[int, str]:
        """Grade tool results by counting back from the tail: full (near-end, large budget) /
        std (middle, standard budget) / stub (old enough to collapse into a one-line
        placeholder). Returns an empty table when grading is disabled (everything is treated
        as std)."""
        if not self.proj_recency_grading_enabled:
            return {}
        full_n = self.proj_recent_tool_full_count
        stub_after = self.proj_tool_stub_after_count
        tiers: Dict[int, str] = {}
        seen = 0
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if not isinstance(m, dict) or m.get("role") != "tool":  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
                continue
            if seen < full_n:
                tiers[i] = "full"
            elif stub_after > 0 and seen >= stub_after:
                tiers[i] = "stub"
            else:
                tiers[i] = "std"
            seen += 1
        return tiers

    def tool_text_for_tier(
        self, content: Any, tier: Optional[str], *, tool_name: Optional[str] = None
    ) -> str:
        """Render tool result text according to its tier. Exempt tools are never stubbed and
        never trimmed."""
        if tool_name and tool_name in self.tool_soft_trim_exempt_names:
            return self.tool_content_to_raw_str(content)
        if tier == "stub":
            raw = self.tool_content_to_raw_str(content)
            if len(raw) <= self.proj_tool_stub_min_chars:
                return self.tool_content_to_trimmed_str(content, tool_name=tool_name)
            return _TOOL_STUB_TEMPLATE.format(n=len(raw))
        if tier == "full":
            return self.soft_trim_tool_text(
                self.tool_content_to_raw_str(content),
                tool_name=tool_name,
                max_chars_override=self.proj_recent_tool_full_max_chars,
            )
        return self.tool_content_to_trimmed_str(content, tool_name=tool_name)

    # ── Soft-trim for user text / tool_call arguments ─────────────────

    def trim_user_content(self, content: Any, max_chars: int) -> Any:
        """A str is trimmed directly; a list form only trims text blocks and leaves image
        blocks alone (images are handled separately by apply_image_recency_policy)."""
        if not self.user_msg_soft_trim_enabled or max_chars <= 0 or content is None:
            return content
        if isinstance(content, str):
            return middle_omit_trim(content, max_chars)
        if isinstance(content, list):
            content = cast(List[Any], content)
            out: List[Any] = []
            for b in content:
                if not isinstance(b, dict):
                    out.append(b)
                    continue
                b = cast(Dict[str, Any], b)
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    out.append({**b, "text": middle_omit_trim(b["text"], max_chars)})
                else:
                    out.append(b)
            return out
        return content

    def trim_tool_calls_args(self, tool_calls: Any, max_chars: int) -> Any:
        """Soft-trim an assistant's tool_calls function.arguments; id/name/structure are left
        untouched."""
        if not self.toolcall_args_soft_trim_enabled or max_chars <= 0:
            return tool_calls
        if not isinstance(tool_calls, list):
            return tool_calls
        tool_calls = cast(List[Any], tool_calls)
        changed = False
        out: List[Any] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                out.append(tc)
                continue
            tc = cast(Dict[str, Any], tc)
            fn = tc.get("function")
            if not isinstance(fn, dict):
                out.append(tc)
                continue
            fn = cast(Dict[str, Any], fn)
            args = fn.get("arguments")
            if isinstance(args, str) and len(args) > max_chars:
                new_fn = {**fn, "arguments": middle_omit_trim(args, max_chars)}
                out.append({**tc, "function": new_fn})
                changed = True
            else:
                out.append(tc)
        return out if changed else tool_calls

    # ── Read-time image policy ─────────────────────────────────────────

    def apply_image_recency_policy(
        self, llm_msgs: List[Dict[str, Any]], keep: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Keep only the most recent `keep` images live; replace earlier image blocks with a
        text placeholder. keep<0 disables this."""
        k = self.proj_recent_image_keep if keep is None else keep
        if k < 0:
            return llm_msgs
        positions: List[Tuple[int, int]] = []
        for mi, m in enumerate(llm_msgs):
            c = m.get("content")
            if isinstance(c, list):
                c = cast(List[Any], c)
                for bj, b in enumerate(c):
                    if is_image_block(b):
                        positions.append((mi, bj))
        if len(positions) <= k:
            return llm_msgs
        drop = set(positions[: len(positions) - k] if k > 0 else positions)
        out: List[Dict[str, Any]] = []
        for mi, m in enumerate(llm_msgs):
            c = m.get("content")
            if not isinstance(c, list):
                out.append(m)
                continue
            c = cast(List[Any], c)
            touched = False
            new_c: List[Any] = []
            for bj, b in enumerate(c):
                if (mi, bj) in drop:
                    new_c.append({"type": "text", "text": _IMAGE_STUB_TEXT})
                    touched = True
                else:
                    new_c.append(b)
            out.append({**m, "content": new_c} if touched else m)
        return out

    # ── Per-message / range weight (the shared ruler for compaction judgment and the builder) ──

    def assistant_projection_char_weight(self, m: Dict[str, Any], *, unit: str = "char") -> int:
        """Body text + tool_calls arguments (after soft-trim)."""
        text, imgs = content_text_and_image_count(m.get("content"))
        w = self.weigh_text(text, unit) + self.image_weight(imgs, unit)
        tcs = m.get("tool_calls")
        if not isinstance(tcs, list):
            return w
        tcs = cast(List[Any], tcs)
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            tc = cast(Dict[str, Any], tc)
            fn_raw = tc.get("function")
            fn: Dict[str, Any] = cast(Dict[str, Any], fn_raw) if isinstance(fn_raw, dict) else {}
            args = fn.get("arguments")
            arg_s = args if isinstance(args, str) else (json.dumps(args, ensure_ascii=False) if args is not None else "")
            if self.toolcall_args_soft_trim_enabled:
                arg_s = middle_omit_trim(arg_s, self.toolcall_args_soft_trim_max_chars)
            w += self.weigh_text(arg_s, unit)
        return w

    def projection_char_weight(
        self,
        msg: Dict[str, Any],
        format_metadata: FormatMetadata,
        *,
        tier: Optional[str] = None,
        unit: str = "char",
        user_max_chars: Optional[int] = None,
    ) -> int:
        """Weight of a single canonical message under the "sent to the primary model" measure.
        For user, this includes the length-equivalent of the metadata-prepended system
        message; for assistant, it includes tool_calls arguments; for tool, it uses the
        post-grading text."""
        if not isinstance(msg, dict):  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
            return 0
        role = msg.get("role") or ""
        if role == "user":
            content = msg.get("content")
            if self.user_msg_soft_trim_enabled:
                budget = user_max_chars if user_max_chars is not None else self.user_msg_soft_trim_max_chars
                content = self.trim_user_content(content, budget)
            text, imgs = content_text_and_image_count(content)
            w = self.weigh_text(text, unit) + self.image_weight(imgs, unit)
            meta = msg.get("metadata")
            if isinstance(meta, dict):
                meta = cast(Dict[str, Any], meta)
                ms = format_metadata(meta).strip()
                if ms:
                    w += self.weigh_text(_METADATA_LINE_PREFIX + ms, unit)
            return w
        if role == "assistant":
            return self.assistant_projection_char_weight(msg, unit=unit)
        if role == "tool":
            return self.weigh_text(self.tool_text_for_tier(msg.get("content"), tier), unit)
        text, imgs = content_text_and_image_count(msg.get("content"))
        return self.weigh_text(text, unit) + self.image_weight(imgs, unit)

    def suffix_l1_totals(
        self,
        messages: List[Dict[str, Any]],
        format_metadata: FormatMetadata,
        *,
        tool_tiers: Optional[Dict[int, str]] = None,
        unit: str = "char",
    ) -> List[int]:
        """suffix[i] = the cumulative weight of messages[i:]; suffix[len]=0."""
        if tool_tiers is None:
            tool_tiers = self.compute_tool_tiers(messages)
        L = len(messages)
        suffix = [0] * (L + 1)
        for i in range(L - 1, -1, -1):
            m = messages[i]
            w = (
                self.projection_char_weight(m, format_metadata, tier=tool_tiers.get(i), unit=unit)
                if isinstance(m, dict)  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
                else 0
            )
            suffix[i] = suffix[i + 1] + w
        return suffix

    def verbatim_slice_char_count(
        self,
        messages: List[Dict[str, Any]],
        start: int,
        end: int,
        format_metadata: FormatMetadata,
        *,
        tool_tiers: Optional[Dict[int, str]] = None,
        unit: str = "char",
    ) -> int:
        """Cumulative weight of messages[start:end) under the "sent to the primary model" measure."""
        if tool_tiers is None:
            tool_tiers = self.compute_tool_tiers(messages)
        total = 0
        for i in range(max(0, start), min(end, len(messages))):
            m = messages[i]
            if isinstance(m, dict):  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
                total += self.projection_char_weight(m, format_metadata, tier=tool_tiers.get(i), unit=unit)
        return total

    def latest_user_regime_b_override(
        self,
        messages: List[Dict[str, Any]],
        tail_start: int,
        end: int,
        format_metadata: FormatMetadata,
        *,
        tool_tiers: Optional[Dict[int, str]] = None,
        inj_chars: int = 0,
        overhead_chars: int = 0,
        hard_max_chars: int = 0,
        safety_chars: int = 4_000,
    ) -> Optional[Tuple[int, int]]:
        """Regime B: the latest user message is preserved by default, and only gets its middle
        trimmed against the remaining char budget if it would push the tail past the hard
        cap. Returns (latest_user_idx, max_chars); returns None if there's no user message or
        this is disabled. Everything runs in the char domain, so the judgment (token
        weighing) and the builder (char soft-trim) land on the exact same cut point.

        ``hard_max_chars``: the char-domain hard cap budget (supplied by the product, usually
        verbatim_hard_max_chars).
        """
        if not self.user_msg_soft_trim_enabled:
            return None
        n = len(messages)
        lo = max(0, tail_start)
        hi = min(end, n)
        latest: Optional[int] = None
        for i in range(hi - 1, lo - 1, -1):
            m = messages[i]
            if isinstance(m, dict) and m.get("role") == "user":  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
                latest = i
                break
        if latest is None:
            return None
        others_c = 0
        for i in range(lo, hi):
            if i == latest:
                continue
            m = messages[i]
            if isinstance(m, dict):  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
                tier = tool_tiers.get(i) if tool_tiers else None
                others_c += self.projection_char_weight(m, format_metadata, tier=tier, unit="char")
        budget = hard_max_chars - max(0, int(inj_chars)) - max(0, int(overhead_chars)) - others_c - safety_chars
        return (latest, max(self.user_latest_msg_min_chars, budget))

    def tail_verbatim_constraints_met(
        self,
        messages: List[Dict[str, Any]],
        tail_start: int,
        format_metadata: FormatMetadata,
        *,
        min_tail_count: int,
        min_tail_messages: int,
        suffix: Optional[List[int]] = None,
        tool_tiers: Optional[Dict[int, str]] = None,
        unit: str = "char",
    ) -> bool:
        """Whether the tool-aligned tail start satisfies MIN_TAIL (both amount and message
        count). Relaxed to "whatever full text can be kept" when the conversation is too
        short."""
        L = len(messages)
        if tail_start < 0 or tail_start > L:
            return False
        if min_tail_count <= 0 and min_tail_messages <= 0:
            return True
        suf = suffix if suffix is not None else self.suffix_l1_totals(messages, format_metadata, tool_tiers=tool_tiers, unit=unit)
        tail_msgs = L - tail_start
        if min_tail_messages > 0:
            need_m = min(min_tail_messages, L)
            if tail_msgs < need_m:
                return False
        if min_tail_count > 0:
            whole = suf[0]
            need_c = min(min_tail_count, whole)
            if suf[tail_start] < need_c:
                return False
        return True

    # ── Read-time projection: canonical → wire ─────────────────────────

    def expand_canonical_to_llm_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        start_idx: int = 0,
        end_idx: Optional[int] = None,
        format_metadata: FormatMetadata,
        tool_tiers: Optional[Dict[int, str]] = None,
        grade: bool = True,
        user_trim_overrides: Optional[Dict[int, int]] = None,
    ) -> List[Dict[str, Any]]:
        """canonical messages[start_idx:end_idx) → the wire form sent to the LLM: user
        metadata is prepended as a system message; tool results are trimmed per their grade;
        user text / tool_call arguments are soft-trimmed at read time; and finally the image
        read-time policy is applied to the whole segment.

        ``user_trim_overrides``: {msg_idx: max_chars} overrides the soft-trim budget for a
        specific user message (regime B uses this for the latest message's remaining
        budget); any user message not in this map falls back to the regime A default.

        ``grade=False``: disables grading, soft-trimming, and the image policy — used for
        the "maximum fidelity, no projection compaction" full-export path.
        """
        fmt = format_metadata
        if not grade:
            tool_tiers = {}
        elif tool_tiers is None:
            tool_tiers = self.compute_tool_tiers(messages)

        def _user_budget(i: int) -> Optional[int]:
            if not grade or not self.user_msg_soft_trim_enabled:
                return None
            if user_trim_overrides and i in user_trim_overrides:
                return user_trim_overrides[i]
            return self.user_msg_soft_trim_max_chars

        end = len(messages) if end_idx is None else min(end_idx, len(messages))
        # tool_call_id → tool name index: must scan the full messages list (a tool result in
        # this window may reference an assistant tool_call from earlier), not just the window.
        tc_id_to_name: Dict[str, str] = {}
        for _m in messages:
            if not isinstance(_m, dict) or _m.get("role") != "assistant":  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
                continue
            _tcs = _m.get("tool_calls")
            if not isinstance(_tcs, list):
                continue
            _tcs = cast(List[Any], _tcs)
            for _tc in _tcs:
                if not isinstance(_tc, dict):
                    continue
                _tc = cast(Dict[str, Any], _tc)
                _tcid = _tc.get("id")
                _fn_raw = _tc.get("function")
                _fn: Dict[str, Any] = cast(Dict[str, Any], _fn_raw) if isinstance(_fn_raw, dict) else {}
                _name = _fn.get("name")
                if isinstance(_tcid, str) and isinstance(_name, str):
                    tc_id_to_name[_tcid] = _name

        out: List[Dict[str, Any]] = []
        for idx in range(max(0, start_idx), end):
            msg = messages[idx]
            if not isinstance(msg, dict):  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
                continue
            meta_val = msg.get("metadata")
            if msg.get("role") == "user" and isinstance(meta_val, dict):
                meta_val = cast(Dict[str, Any], meta_val)
                meta_str = fmt(meta_val)
                if meta_str:
                    out.append({"role": "system", "content": _METADATA_LINE_PREFIX + meta_str})
                ub = _user_budget(idx)
                uc = msg.get("content")
                out.append({"role": "user", "content": self.trim_user_content(uc, ub) if ub is not None else uc})
            else:
                role = msg.get("role")
                content: Any = msg.get("content")
                if role == "tool":
                    tcid = msg.get("tool_call_id")
                    tname = tc_id_to_name.get(tcid) if isinstance(tcid, str) else None
                    content = self.tool_text_for_tier(content, tool_tiers.get(idx), tool_name=tname)
                elif role == "user":
                    ub = _user_budget(idx)
                    if ub is not None:
                        content = self.trim_user_content(content, ub)
                out.append({"role": role, "content": content})
                llm_msg = out[-1]
                if "tool_calls" in msg:
                    llm_msg["tool_calls"] = (
                        self.trim_tool_calls_args(msg["tool_calls"], self.toolcall_args_soft_trim_max_chars)
                        if grade
                        else msg["tool_calls"]
                    )
                if "tool_call_id" in msg:
                    llm_msg["tool_call_id"] = msg["tool_call_id"]
        if not grade:
            return out
        return self.apply_image_recency_policy(out)


# ---------------------------------------------------------------------------
# Re-injection alongside the compaction summary (the most recent tool results
# from the compacted region are restored verbatim)
# ---------------------------------------------------------------------------

REINJECT_SYSTEM_TAG = (
    "Restored context — these tool-call outputs from earlier in this conversation are "
    "reproduced verbatim (after L1 soft-trim) for continuity. Their surrounding turns are "
    "compressed into the summary above; this block keeps the actual results addressable.\n\n"
)

SUMMARY_SYSTEM_TAG = (
    "Prior conversation (compressed checkpoint). "
    "The UI and database retain full verbatim messages; only this summary is injected for context economy.\n\n"
)


def gather_recent_tool_results_for_reinject(
    config: ProjectionConfig,
    messages: List[Dict[str, Any]],
    covers_exclusive_end: int,
    *,
    k: int,
    budget_chars: int,
) -> List[Tuple[int, str, str]]:
    """Scan backward through the "compacted region" (messages[0:covers_exclusive_end)) and
    pick the most recent k role=tool results, labeling each with its parent assistant's
    tool_call name/arguments, returned in original chronological (ascending) order. The
    summary compresses tool-call outputs into abstract bullet points, but continuing the
    conversation often requires the actual content of those outputs."""
    if k <= 0 or budget_chars <= 0 or covers_exclusive_end <= 0:
        return []
    upper = min(int(covers_exclusive_end), len(messages))
    tc_lookup: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for i in range(upper):
        m = messages[i]
        if not isinstance(m, dict) or m.get("role") != "assistant":  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
            continue
        tcs = m.get("tool_calls")
        if not isinstance(tcs, list):
            continue
        tcs = cast(List[Any], tcs)
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            tc = cast(Dict[str, Any], tc)
            tcid = tc.get("id")
            if isinstance(tcid, str) and tcid:
                tc_lookup[tcid] = (i, tc)

    picked: List[Tuple[int, str, str]] = []
    used = 0
    for i in range(upper - 1, -1, -1):
        m = messages[i]
        if not isinstance(m, dict) or m.get("role") != "tool":  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
            continue
        text = config.tool_content_to_trimmed_str(m.get("content"))
        if len(text.strip()) < 200:
            continue
        tcid = m.get("tool_call_id")
        tc_info = tc_lookup.get(tcid) if isinstance(tcid, str) else None
        if tc_info is not None:
            _, tc = tc_info
            fn_raw = tc.get("function")
            fn: Dict[str, Any] = cast(Dict[str, Any], fn_raw) if isinstance(fn_raw, dict) else {}
            name = fn.get("name") or "?"
            args_raw = fn.get("arguments") or ""
            if not isinstance(args_raw, str):
                try:
                    args_raw = json.dumps(args_raw, ensure_ascii=False)
                except (TypeError, ValueError):
                    args_raw = str(args_raw)
            if len(args_raw) > 240:
                args_raw = args_raw[:240] + "..."
            label = f"{name}({args_raw})"
        else:
            label = f"tool_call_id={tcid or '?'}"

        item_size = len(label) + len(text) + 64
        if used + item_size > budget_chars:
            break
        picked.append((i, label, text))
        used += item_size
        if len(picked) >= k:
            break

    picked.sort(key=lambda t: t[0])
    return picked


def format_reinject_system_content(items: List[Tuple[int, str, str]]) -> Optional[str]:
    if not items:
        return None
    parts: List[str] = [REINJECT_SYSTEM_TAG]
    for idx, label, text in items:
        parts.append(f"## [msg #{idx}] {label}")
        parts.append(text)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def build_llm_messages_with_context_projection(
    config: ProjectionConfig,
    messages: List[Dict[str, Any]],
    active: Optional[CompactionRecord],
    *,
    format_metadata: FormatMetadata,
    reinject_tool_results_count: int = 0,
    reinject_budget_chars: int = 0,
    hard_max_chars: int = 0,
) -> List[Dict[str, Any]]:
    """Build the messages sent to the LLM based on the currently active compaction record:
    1 system message (the summary) + optionally 1 more system message (re-injecting the most
    recent tool results from the compacted region) + the tail expanded verbatim. Equivalent
    to a full expansion when there's no active record or the record is no longer valid.

    Does NOT perform repair — this is a pure function. Whether "active is still within a
    valid range, or a different record should be reselected from the archive" is the concern
    of :func:`repair_active`; callers should run repair first and persist/update active
    before calling this.
    """
    tool_tiers = config.compute_tool_tiers(messages)

    def _regime_b_overrides(t_start: int, inj_chars: int) -> Optional[Dict[int, int]]:
        ov = config.latest_user_regime_b_override(
            messages, t_start, len(messages), format_metadata,
            tool_tiers=tool_tiers, inj_chars=inj_chars, overhead_chars=0,
            hard_max_chars=hard_max_chars,
        )
        return {ov[0]: ov[1]} if ov is not None else None

    if (
        active is None
        or not active.summary_text.strip()
        or active.covers_exclusive_end < 0
        or active.covers_exclusive_end > len(messages)
    ):
        return config.expand_canonical_to_llm_messages(
            messages, format_metadata=format_metadata, tool_tiers=tool_tiers,
            user_trim_overrides=_regime_b_overrides(0, 0),
        )

    summary_text = active.summary_text.strip()
    tail_start = adjust_tail_start_for_tool_protocol(messages, active.covers_exclusive_end)
    tail = config.expand_canonical_to_llm_messages(
        messages,
        start_idx=tail_start,
        format_metadata=format_metadata,
        tool_tiers=tool_tiers,
        user_trim_overrides=_regime_b_overrides(tail_start, len(SUMMARY_SYSTEM_TAG + summary_text)),
    )
    prefix_msgs: List[Dict[str, Any]] = [{"role": "system", "content": SUMMARY_SYSTEM_TAG + summary_text}]

    if reinject_tool_results_count > 0 and reinject_budget_chars > 0:
        picks = gather_recent_tool_results_for_reinject(
            config, messages, active.covers_exclusive_end,
            k=reinject_tool_results_count, budget_chars=reinject_budget_chars,
        )
        content = format_reinject_system_content(picks)
        if content:
            prefix_msgs.append({"role": "system", "content": content})

    return prefix_msgs + tail


# ---------------------------------------------------------------------------
# Summary quality gate: detects whether the compaction model has fallen into one of
# three degeneration modes — switching to another language / repetition loops / a
# perfunctorily-short summary
# ---------------------------------------------------------------------------


def cjk_char_ratio(s: str) -> float:
    """Fraction of CJK Unified Ideographs + Hiragana + Katakana characters, used to detect
    whether the compaction model silently switched languages."""
    if not s:
        return 0.0
    total = len(s)
    cnt = 0
    for ch in s:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF:
            cnt += 1
    return cnt / max(1, total)


def detect_repeated_lines(s: str, *, threshold: int = 3, min_line_len: int = 30) -> Optional[str]:
    """Returns the first "non-short" line (after stripping) that appears ≥threshold times,
    which indicates generation got stuck in a repetition loop. Returns None if there's no
    repetition."""
    if not s:
        return None
    counts: Dict[str, int] = {}
    for line in s.split("\n"):
        l = line.strip()
        if len(l) < min_line_len:
            continue
        counts[l] = counts.get(l, 0) + 1
        if counts[l] >= threshold:
            return l
    return None


def quality_gate_check(
    summary_text: str, transcript: str, *, prev_text: str = "", enabled: bool = True
) -> Optional[str]:
    """Returns None on pass; otherwise returns a failure-reason string. Checks:
    1) Length floor: summary < max(800, 0.4 × len(prev_text))
    2) Language shift: transcript CJK ratio > 30% but summary CJK ratio drops by 25+ points
    3) Repetition loop: a long line repeated ≥3 times
    """
    if not enabled:
        return None
    if not summary_text:
        return "empty_summary"

    floor = 800
    if prev_text:
        floor = max(floor, int(0.4 * len(prev_text)))
    if len(summary_text) < floor:
        return f"too_short(len={len(summary_text)}, floor={floor})"

    tr_cjk = cjk_char_ratio(transcript)
    sm_cjk = cjk_char_ratio(summary_text)
    if tr_cjk > 0.30 and sm_cjk + 0.25 < tr_cjk:
        return f"language_shift(transcript_cjk={tr_cjk:.2f}, summary_cjk={sm_cjk:.2f})"

    rep = detect_repeated_lines(summary_text)
    if rep is not None:
        rep_preview = rep[:80] + ("..." if len(rep) > 80 else "")
        return f"repetition_detected: {rep_preview!r}"

    return None


# ---------------------------------------------------------------------------
# Summary prompt + transcript serialization + default summarizer agent
# ---------------------------------------------------------------------------

#: A single prompt: every time, it's regenerated from scratch off the full transcript of
#: messages[0..new_until) — the previous summary is never fed back in for a merge. This is
#: what prevents generational decay ("the previous generation's glitches/degeneration get
#: merged into the next one").
FROM_SCRATCH_SUMMARY_PROMPT = """You compress a conversation transcript so another assistant can continue working seamlessly from this checkpoint.

LANGUAGE: Write the summary in the SAME LANGUAGE as the bulk of the transcript. If the transcript is predominantly Chinese (Han characters), the summary MUST be in Chinese — do NOT switch to English bullet style. If mixed, follow the primary language; only use the other language inside verbatim quotes.

LENGTH: Aim for a substantial summary — typically 4000–10000 characters for a long transcript. Do not abbreviate aggressively; preserve specificity over brevity. The downstream assistant relies entirely on this summary for everything older than the verbatim tail.

PRESERVE VERBATIM where they appear:
- File paths, document IDs, URLs, model names, error strings, numeric metrics (PSNR / SSIM / sizes / counts / timestamps)
- Every user-stated rule, convention, or agreement — quote the user's own wording verbatim, using an attribution phrase in the transcript's own language (e.g. `the user said: "..."`, or its equivalent in Chinese if the transcript is in Chinese). These are load-bearing for future behavior.
- The user's intent and goal across the most recent turns (do not paraphrase it away)
- Key tool-call results the user later referenced (file reads, command outputs, document reads)

OUTLINE (use these section headings, in this order; keep a heading even if its body is short):

## Goals
## Constraints / preferences  (← include all user-stated rules / conventions / "from now on …" agreements; quote the user)
## Progress (done / in progress / blocked)
## Key decisions
## Open questions / TODOs
## Critical references (paths, IDs, links, key tool-call results — paste enough of each result that the next assistant can use it without re-running the tool)
## Recent context (turn-by-turn summary of the last ~10 user/assistant turns so continuity is preserved)

Transcript:
---
{transcript}
---

Output ONLY the structured summary. No preamble. No closing remarks. Match the transcript's language."""

#: The system-role text for ``_call_summary_llm`` (the default value used by
#: default_summarize; can be overridden).
DEFAULT_SUMMARIZER_SYSTEM_PROMPT = (
    "You compress conversation transcripts into faithful, language-preserving "
    "structured notes. Output only the requested sections. NEVER switch the output "
    "language away from the transcript's primary language (in particular, do not "
    "translate Chinese conversations into English summaries)."
)


def serialize_messages_for_summary(config: ProjectionConfig, messages: List[Dict[str, Any]], start: int, end: int) -> str:
    """Half-open interval logic: [start, end), consistent with Python slicing. Tool lines
    use the same soft-trim as the primary model path."""
    lines: List[str] = []
    for i in range(max(0, start), min(end, len(messages))):
        m = messages[i]
        if not isinstance(m, dict):  # pyright: ignore[reportUnnecessaryIsInstance] —— messages come from the caller's persistence layer; at runtime they may not actually be the declared Dict shape (legacy/malformed records)
            continue
        role = m.get("role") or ""
        if role == "user":
            lines.append(f"[User]: {text_from_content(m.get('content'))}")
        elif role == "assistant":
            tcs = m.get("tool_calls")
            body = text_from_content(m.get("content"))
            if body:
                lines.append(f"[Assistant]: {body}")
            if isinstance(tcs, list) and tcs:
                tcs = cast(List[Any], tcs)
                for tc in tcs[:20]:
                    if not isinstance(tc, dict):
                        continue
                    tc = cast(Dict[str, Any], tc)
                    fn_raw = tc.get("function")
                    fn: Dict[str, Any] = cast(Dict[str, Any], fn_raw) if isinstance(fn_raw, dict) else {}
                    name = fn.get("name") or ""
                    args = fn.get("arguments") or ""
                    arg_s = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                    if len(arg_s) > 1200:
                        arg_s = arg_s[:1200] + "..."
                    lines.append(f"[Assistant tool]: {name}({arg_s})")
        elif role == "tool":
            s = config.tool_content_to_trimmed_str(m.get("content"))
            tid = m.get("tool_call_id") or ""
            lines.append(f"[Tool {tid}]: {s}")
        else:
            lines.append(f"[{role}]: {text_from_content(m.get('content'))}")
    return "\n\n".join(lines)


def build_from_scratch_transcript(
    config: ProjectionConfig,
    messages: List[Dict[str, Any]],
    new_until: int,
    format_metadata: FormatMetadata,
    *,
    max_transcript_chars: int,
) -> str:
    """Build the full transcript of messages[0:new_until). When it exceeds
    ``max_transcript_chars``, hard-truncate from the earliest end (keeping the recent end,
    with a leading truncation marker) — the tradeoff that lets the new summary generation
    avoid depending on the previous one (breaking generational decay).

    ``format_metadata`` is only used to estimate "which index can we truncate at and still
    fit the budget" — this should be the same product format_metadata used by the read-time
    projection, otherwise the size estimated here won't match the size actually sent to the
    LLM, and the truncation point will be miscalculated.
    """
    end = max(0, min(int(new_until), len(messages)))
    if end <= 0:
        return ""

    suffix = config.suffix_l1_totals(messages, format_metadata, unit="char")
    full_size = suffix[0] - suffix[end] if end <= len(suffix) - 1 else suffix[0]
    if full_size <= max_transcript_chars:
        return serialize_messages_for_summary(config, messages, 0, end)

    start = 0
    for i in range(0, end):
        size_from_i = suffix[i] - suffix[end] if end <= len(suffix) - 1 else (suffix[i] - 0)
        if size_from_i <= max_transcript_chars:
            start = i
            break

    body = serialize_messages_for_summary(config, messages, start, end)
    if start > 0:
        prelude = (
            f"[Earlier {start} message(s) truncated due to transcript cap "
            f"({full_size} chars > {max_transcript_chars}); their content is NOT included in this compaction "
            f"run. The downstream agent only sees the summary derived from messages[{start}:{end}). "
            f"Use the existing 'Critical references' / 'Recent context' sections to mention "
            f"anything in those earlier messages you still know about from prior context.]\n\n"
        )
        return prelude + body
    return body


async def default_summarize(
    complete: Callable[..., Any],
    *,
    model: str,
    transcript: str,
    max_output_tokens: int,
    prompt_template: str = FROM_SCRATCH_SUMMARY_PROMPT,
    system_prompt: str = DEFAULT_SUMMARIZER_SYSTEM_PROMPT,
    temperature: float = 0.2,
) -> str:
    """The default summarizer agent: build the prompt → call the injected ``complete``
    callback → extract the text.

    ``complete(model=, messages=, max_tokens=, temperature=) -> str`` (can be sync or a
    coroutine, both are accepted) — the product plugs in its own multi-provider routing /
    BYOK / proxy env through this; the framework doesn't care which provider is actually
    used. The signature is deliberately narrower than
    :class:`~flops_agent.seams.llm_client.LLMStreamClient` (a single complete-text return
    rather than a chunk-by-chunk stream) — summarization is a one-shot background action
    that doesn't need to stream to the user.
    """
    user_prompt = prompt_template.format(transcript=transcript)
    result = complete(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_output_tokens,
        temperature=temperature,
    )
    if hasattr(result, "__await__"):
        result = await result
    return (result or "").strip()


# ---------------------------------------------------------------------------
# Compaction triggering / split-point planning / repair and rehoming of the active record
# ---------------------------------------------------------------------------


@dataclass
class CompactionPolicy:
    """Compaction's thresholds and triggering algorithm. The numbers are product policy
    (defaults = production-validated practice); the algorithm is framework mechanism.

    All window-related methods in the token domain accept an already-resolved
    ``window_usable: int`` (0 = unavailable, falling back to the absolute defaults) — how
    the model window is looked up (ops parameter table / resource node / curated table /
    provider) is the product's concern.
    """

    enabled: bool = False

    # Absolute-value fallback (used when the window is unavailable or below small_window_floor_tokens)
    verbatim_min_tokens: int = 0
    verbatim_max_tokens_abs: int = 64_000
    verbatim_hard_max_tokens_abs: int = 80_000
    verbatim_min_tail_tokens_abs: int = 20_000
    verbatim_min_tail_messages: int = 0

    # Percentage scheme (used when the window is available)
    max_window_fraction: float = 0.66
    min_window_fraction: float = 0.33
    small_window_floor_tokens: int = 20_000
    #: Cap on min-tail as a fraction of the model window, preventing the feasible range from
    #: collapsing on small windows.
    min_tail_window_fraction: float = 0.25

    # Other knobs for the judgment itself
    ignore_system_overhead: bool = False
    reserved_tail_messages: int = 20
    quality_gate_enabled: bool = True
    max_output_tokens: int = 16_384
    max_transcript_chars: int = 80_000
    reinject_tool_results_count: int = 6
    reinject_budget_chars: int = 24_000

    # Char domain (display endpoints + the source of regime B's budget; deliberately kept
    # as a separate measure from the token-domain judgment)
    verbatim_min_chars: int = 8_000
    verbatim_max_chars: int = 100_000
    verbatim_hard_max_chars: int = 150_000
    verbatim_min_tail_chars: int = 0

    def _window_usable(self, window_usable: int) -> int:
        return window_usable if window_usable >= self.small_window_floor_tokens else 0

    def verbatim_max_tokens(self, window_usable: int) -> int:
        """Soft trigger line: once the total exceeds this, (asynchronous) compaction kicks in."""
        w = self._window_usable(window_usable)
        if w > 0:
            return max(1, int(w * self.max_window_fraction))
        return max(1, self.verbatim_max_tokens_abs)

    def verbatim_hard_max_tokens(self, window_usable: int) -> int:
        """Hard cap: once the total exceeds this, compaction must be drained before the
        primary model can be called. Derived from the soft trigger line."""
        soft = self.verbatim_max_tokens(window_usable)
        w = self._window_usable(window_usable)
        if w > 0:
            return max(soft + 1, int(w * 0.95))
        return max(soft + 1, self.verbatim_hard_max_tokens_abs)

    def verbatim_min_tail_tokens(self, window_usable: int) -> int:
        """Verbatim tail floor (tokens). The absolute config value is capped by the model
        window to prevent it from conflicting with the trigger line/target on small windows
        (min-tail is an absolute floor while the trigger line/target are window percentages;
        on a small window the two can become mutually exclusive and force a deadlock)."""
        w = self._window_usable(window_usable)
        if w <= 0:
            return self.verbatim_min_tail_tokens_abs
        return max(0, min(self.verbatim_min_tail_tokens_abs, int(w * self.min_tail_window_fraction)))

    def verbatim_bounds_tok(self, window_usable: int) -> Tuple[int, int]:
        """Used by repair / rehome to judge "is active still within the acceptable range":
        (lower bound, upper bound = the soft trigger line)."""
        mn = self.verbatim_min_tokens
        mx = self.verbatim_max_tokens(window_usable)
        if mx <= mn:
            mx = mn + 1
        return mn, mx

    def thresholds(self, window_usable: int) -> Tuple[int, int, int]:
        """Returns (trigger, target_lo, target_hi) in token units, for :meth:`plan` to use.

        Window available: percentage scheme — trigger = window × max_frac, target_hi =
        window × min_frac (the split-point target ceiling), target_lo = target_hi - slack
        (naturally forming a hysteresis band to prevent oscillation). Small-window guardrail:
        target_hi must clear the min-tail floor by a sufficient margin, otherwise the
        feasible range collapses.
        Window unavailable: absolute fallback — trigger = target_hi = the soft trigger line,
        target_lo = verbatim_min_tokens.
        """
        trigger = self.verbatim_max_tokens(window_usable)
        w = self._window_usable(window_usable)
        if w <= 0:
            return trigger, self.verbatim_min_tokens, trigger
        target_hi = max(1, int(w * self.min_window_fraction))
        min_tail = self.verbatim_min_tail_tokens(window_usable)
        margin = max(4_000, min_tail // 2)
        floor_hi = min_tail + margin
        if target_hi < floor_hi:
            target_hi = min(trigger - 1, floor_hi) if trigger - 1 > floor_hi else floor_hi
        slack = max(1, target_hi // 4)
        target_lo = max(0, target_hi - slack)
        return trigger, target_lo, target_hi

    def decision_overhead_tokens(self, estimator: ProjectionConfig, primary_system_l1_chars: int, tools_schema_l1_chars: int) -> int:
        """Fixed overhead used by the compaction judgment (token units). Not ignored by
        default — the percentage trigger line is well above this fixed overhead, and the
        system prompt / tool schema already occupy part of the window, so they shouldn't be
        excluded from the judgment."""
        if self.ignore_system_overhead:
            return 0
        return estimator.approx_tokens_from_chars(int(primary_system_l1_chars) + int(tools_schema_l1_chars))

    def summary_injection_tokens(self, estimator: ProjectionConfig, summary_text: str) -> int:
        return estimator.estimate_tokens(SUMMARY_SYSTEM_TAG + (summary_text or ""))


def compute_ideal_covers_exclusive_end(
    policy: CompactionPolicy,
    estimator: ProjectionConfig,
    messages: List[Dict[str, Any]],
    min_c: int,
    max_c: int,
    format_metadata: FormatMetadata,
    *,
    overhead: int = 0,
    window_usable: int = 0,
) -> int:
    """Choose the split-point index n (the summary covers messages[0:n); after tool
    alignment, the tail is sent to the model verbatim), such that the total ≈ overhead +
    suffix[tail_start] falls within [min_c, max_c]. When multiple candidates qualify, take
    the **largest** n (fold as much as possible into the summary). When no candidate falls
    within the legal range, first try to get as close to ≤ max_c as possible, then fall back
    to the largest n that is ≥ min_c.

    Reserved tail floor (an anti-amnesia guardrail): candidate n may never exceed
    ``L - reserved_tail_messages``, guaranteeing that the most recent N raw messages are
    never folded into the summary.
    """
    L = len(messages)
    if L <= 0:
        return 0
    oh = max(0, int(overhead))
    mc = max(0, min_c)
    xc = max(mc + 1, max_c)

    reserved_n = policy.reserved_tail_messages
    upper_n_inclusive = max(0, L - 1 - reserved_n) if L > reserved_n else max(0, L - 1)

    suffix = estimator.suffix_l1_totals(messages, format_metadata, unit="token")
    min_tail_tok = policy.verbatim_min_tail_tokens(window_usable)

    def _tail_ok(ts: int) -> bool:
        return estimator.tail_verbatim_constraints_met(
            messages, ts, format_metadata,
            min_tail_count=min_tail_tok, min_tail_messages=policy.verbatim_min_tail_messages,
            suffix=suffix, unit="token",
        )

    def _tot_and_tail(n: int) -> Tuple[int, int]:
        ts = adjust_tail_start_for_tool_protocol(messages, n)
        return oh + suffix[ts], ts

    raw = -1
    for n in range(upper_n_inclusive, -1, -1):
        tot, ts = _tot_and_tail(n)
        if not _tail_ok(ts):
            continue
        if mc <= tot <= xc:
            raw = n
            break
    if raw < 0:
        for n in range(upper_n_inclusive, -1, -1):
            tot, ts = _tot_and_tail(n)
            if not _tail_ok(ts):
                continue
            if tot <= xc:
                raw = n
                break
    if raw < 0:
        for n in range(upper_n_inclusive, -1, -1):
            tot, ts = _tot_and_tail(n)
            if not _tail_ok(ts):
                continue
            if tot >= mc:
                raw = n
                break
    if raw < 0:
        raw = 0
    if raw >= L:
        raw = max(0, L - 1)
    return adjust_tail_start_for_tool_protocol(messages, raw)


@dataclass
class CompactionPlan:
    """Result of :func:`plan_new_compaction`: a new summary covering
    ``messages[0:new_until)`` needs to be created."""

    prev_end: int
    prev_text: str
    new_until: int


def plan_new_compaction(
    policy: CompactionPolicy,
    estimator: ProjectionConfig,
    messages: List[Dict[str, Any]],
    active: Optional[CompactionRecord],
    format_metadata: FormatMetadata,
    *,
    primary_system_l1_chars: int = 0,
    tools_schema_l1_chars: int = 0,
    window_usable: int = 0,
) -> Optional[CompactionPlan]:
    """Read-only: decides whether a new compaction needs to be triggered + computes the
    split point. ``None`` means no compaction is needed.

    Purely percentage-driven (when the window is available): trigger/target_lo/target_hi
    are described in :meth:`CompactionPolicy.thresholds`, and they naturally form a
    hysteresis band — after compacting down to ~target_hi, the total has to climb back up
    to trigger before compaction fires again.

    The returned ``prev_text`` is only used by the quality gate for the length-floor
    comparison — the new summary generation is always regenerated from scratch off the full
    transcript of ``messages[0:new_until)``; the previous summary is never fed in for a
    merge, which is what avoids generational decay (the previous generation's
    glitches/degeneration being carried forward, unmodified, into the next one).
    """
    if not policy.enabled or len(messages) < 2:
        return None

    trigger, target_lo, target_hi = policy.thresholds(window_usable)
    overhead_fixed = policy.decision_overhead_tokens(estimator, primary_system_l1_chars, tools_schema_l1_chars)

    prev_end = 0
    prev_text = ""
    if active is not None:
        prev_end = max(0, active.covers_exclusive_end)
        prev_text = (active.summary_text or "").strip()

    prev_inj = policy.summary_injection_tokens(estimator, prev_text) if prev_text else 0
    overhead = overhead_fixed + prev_inj
    tail_now = estimator.verbatim_slice_char_count(messages, prev_end, len(messages), format_metadata, unit="token")
    if overhead + tail_now <= trigger:
        return None

    new_until = compute_ideal_covers_exclusive_end(
        policy, estimator, messages, target_lo, target_hi, format_metadata,
        overhead=overhead, window_usable=window_usable,
    )
    if new_until <= 0 or new_until >= len(messages) or new_until <= prev_end:
        return None
    return CompactionPlan(prev_end=prev_end, prev_text=prev_text, new_until=new_until)


@dataclass
class RepairOutcome:
    """Result of :func:`repair_active` / :func:`try_rehome_from_archive`. ``changed=False``
    means nothing needs to change; the caller doesn't need to write anything back."""

    changed: bool
    new_active_id: Optional[str] = None
    prune_above_exclusive_end: Optional[int] = None


def try_rehome_from_archive(
    policy: CompactionPolicy,
    estimator: ProjectionConfig,
    messages: List[Dict[str, Any]],
    archive: List[CompactionRecord],
    msg_len: int,
    format_metadata: FormatMetadata,
    *,
    primary_system_l1_chars: int = 0,
    tools_schema_l1_chars: int = 0,
    window_usable: int = 0,
) -> Optional[RepairOutcome]:
    """Search the archive for a record with ``0 <= covers_exclusive_end <= msg_len`` whose
    total L1 token count falls within ``[MIN, trigger line]``; among multiple candidates,
    take the one with the largest ``covers_exclusive_end``. ``None`` means no candidate was
    found (the caller decides, based on this, whether to clear active or leave it as-is)."""
    if msg_len <= 0:
        return None
    lo_c, hi_c = policy.verbatim_bounds_tok(window_usable)
    overhead = policy.decision_overhead_tokens(estimator, primary_system_l1_chars, tools_schema_l1_chars)
    suffix = estimator.suffix_l1_totals(messages, format_metadata, unit="token")
    min_tail_tok = policy.verbatim_min_tail_tokens(window_usable)

    best_e = -1
    best_id: Optional[str] = None
    for rec in archive:
        e = rec.covers_exclusive_end
        if e < 0 or e > msg_len or e == msg_len:
            continue
        st = (rec.summary_text or "").strip()
        if len(st) < 40 or not rec.id:
            continue
        inj = policy.summary_injection_tokens(estimator, st)
        ts = adjust_tail_start_for_tool_protocol(messages, e)
        if not estimator.tail_verbatim_constraints_met(
            messages, ts, format_metadata,
            min_tail_count=min_tail_tok, min_tail_messages=policy.verbatim_min_tail_messages,
            suffix=suffix, unit="token",
        ):
            continue
        tail_chars = estimator.verbatim_slice_char_count(messages, ts, msg_len, format_metadata, unit="token")
        combined = overhead + inj + tail_chars
        if combined < lo_c or combined > hi_c:
            continue
        if e > best_e:
            best_e = e
            best_id = rec.id

    if best_id is None:
        return None
    return RepairOutcome(changed=True, new_active_id=best_id, prune_above_exclusive_end=best_e)


def repair_active(
    policy: CompactionPolicy,
    estimator: ProjectionConfig,
    messages: List[Dict[str, Any]],
    active: Optional[CompactionRecord],
    archive: List[CompactionRecord],
    format_metadata: FormatMetadata,
    *,
    active_id: Optional[str] = None,
    primary_system_l1_chars: int = 0,
    tools_schema_l1_chars: int = 0,
    window_usable: int = 0,
) -> RepairOutcome:
    """Fix up active: if its length is out of range, or its current total L1 is not within
    ``[MIN, trigger line]``, reselect a record from the archive (clearing active if no
    candidate is found). Callers should run this before every occasion where they're about
    to actually use active to build the wire payload / decide whether new compaction is
    needed, then apply the resulting ``RepairOutcome`` to the
    :class:`~flops_agent.seams.compaction_store.CompactionStore` (``changed=False`` means do
    nothing).

    ``active_id``: the id of the active record the session currently points to (pass the
    original id in even if it's already a dangling pointer with no matching record in
    ``archive``) — ``active is None`` alone can't distinguish "never set" from "was pointing
    at a record that's no longer in the archive." The former requires no action; the latter
    must clear the dangling pointer (``changed=True, new_active_id=None``), otherwise
    downstream code will keep retrying to resolve the same unresolvable id.
    """
    L = len(messages)
    lo_c, hi_c = policy.verbatim_bounds_tok(window_usable)
    overhead = policy.decision_overhead_tokens(estimator, primary_system_l1_chars, tools_schema_l1_chars)

    if active is None:
        had_dangling_id = bool(active_id)
        rehome = try_rehome_from_archive(
            policy, estimator, messages, archive, L, format_metadata,
            primary_system_l1_chars=primary_system_l1_chars, tools_schema_l1_chars=tools_schema_l1_chars,
            window_usable=window_usable,
        )
        if rehome is not None:
            return rehome
        if had_dangling_id:
            return RepairOutcome(changed=True, new_active_id=None)
        return RepairOutcome(changed=False)

    end_exc = active.covers_exclusive_end
    stale_len = end_exc < 0 or end_exc > L or end_exc == L
    if not stale_len:
        inj = policy.summary_injection_tokens(estimator, (active.summary_text or "").strip())
        tail_start = adjust_tail_start_for_tool_protocol(messages, end_exc)
        tail_c = estimator.verbatim_slice_char_count(messages, tail_start, L, format_metadata, unit="token")
        combined = overhead + inj + tail_c
        min_tail_tok = policy.verbatim_min_tail_tokens(window_usable)
        tail_ok = estimator.tail_verbatim_constraints_met(
            messages, tail_start, format_metadata,
            min_tail_count=min_tail_tok, min_tail_messages=policy.verbatim_min_tail_messages, unit="token",
        )
        if lo_c <= combined <= hi_c and tail_ok:
            return RepairOutcome(changed=False)
        rehome = try_rehome_from_archive(
            policy, estimator, messages, archive, L, format_metadata,
            primary_system_l1_chars=primary_system_l1_chars, tools_schema_l1_chars=tools_schema_l1_chars,
            window_usable=window_usable,
        )
        if rehome is not None:
            return rehome
        return RepairOutcome(changed=True, new_active_id=None)

    rehome = try_rehome_from_archive(
        policy, estimator, messages, archive, L, format_metadata,
        primary_system_l1_chars=primary_system_l1_chars, tools_schema_l1_chars=tools_schema_l1_chars,
        window_usable=window_usable,
    )
    if rehome is not None:
        return rehome
    return RepairOutcome(changed=True, new_active_id=None)


__all__ = [
    "FormatMetadata",
    "ProjectionConfig",
    "is_image_block",
    "content_text_and_image_count",
    "text_from_content",
    "middle_omit_trim",
    "adjust_tail_start_for_tool_protocol",
    "SUMMARY_SYSTEM_TAG",
    "REINJECT_SYSTEM_TAG",
    "gather_recent_tool_results_for_reinject",
    "format_reinject_system_content",
    "build_llm_messages_with_context_projection",
    "cjk_char_ratio",
    "detect_repeated_lines",
    "quality_gate_check",
    "FROM_SCRATCH_SUMMARY_PROMPT",
    "DEFAULT_SUMMARIZER_SYSTEM_PROMPT",
    "serialize_messages_for_summary",
    "build_from_scratch_transcript",
    "default_summarize",
    "CompactionPolicy",
    "CompactionPlan",
    "RepairOutcome",
    "compute_ideal_covers_exclusive_end",
    "plan_new_compaction",
    "try_rehome_from_archive",
    "repair_active",
]
