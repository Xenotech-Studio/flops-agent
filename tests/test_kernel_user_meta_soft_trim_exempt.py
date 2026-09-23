"""isMeta user notifications must never be middle-omitted by the read-time soft-trim.

Regression: a forwarded-WeChat / background-task notification is injected as a
``role=user, isMeta=True`` message. Once it is no longer the *latest* user message (regime B),
regime A's 600-char ``middle_omit_trim`` used to cut its middle out -- corrupting the numbered
list inside (the reported "~176 chars missing from the middle") with no way for the agent to
recover the elided span. It must now pass through untouched, while a genuine large *user paste*
in the same historical position is still trimmed.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent.engine.compaction import ProjectionConfig  # noqa: E402


def _fmt(_meta):
    return ""


def _wechat_notification_body() -> str:
    head = "【微信新消息】\n- 会话: 项目群\n\n【正文】\n"
    entries = "\n".join(
        f"{i}. 发言人{i}：这是第 {i} 条转发的聊天记录明细，内容足够长以撑开正文，"
        f"确保整段正文远超过 regime A 的 600 字符预算，从而触发中段省略；补一些字数占位。" for i in range(1, 8)
    )
    tail = "\n\n这条来自你挂的微信监听器。要回复用 resource_node_wechat_send。"
    return head + entries + tail


def _project(messages):
    cfg = ProjectionConfig()  # defaults: soft-trim on, regime A = 600 chars, exempt_meta = True
    # No regime-B override -> every user message weighed/built under regime A (600).
    out = cfg.expand_canonical_to_llm_messages(messages, format_metadata=_fmt)
    return [m for m in out if m.get("role") == "user"]


def test_ismeta_notification_survives_soft_trim():
    body = _wechat_notification_body()
    assert len(body) > 600, "fixture must exceed regime-A budget to exercise the trim"
    messages = [
        {"role": "user", "content": body, "isMeta": True, "kind": "task_event"},
        {"role": "user", "content": "后面再来一条真实用户消息，把 isMeta 挤成历史（非最新）。"},
    ]
    users = _project(messages)
    injected = users[0]["content"]
    # Full body preserved -- including the middle entries that used to be omitted.
    assert injected == body, "isMeta notification must not be trimmed"
    for i in range(1, 8):
        assert f"\n{i}. " in ("\n" + injected), f"entry {i} missing from projected body"
    assert "…" not in injected and "字符" not in injected.split("【正文】")[1].split("这条来自")[0][:5], \
        "no middle-omission marker expected in an exempt message"


def test_real_user_paste_still_trimmed():
    """The exemption is scoped to isMeta -- a plain large user paste in the same historical
    slot is still middle-omitted, so we didn't disable the feature wholesale."""
    paste = "".join(f"行{i} 这是一段很长的粘贴内容。" for i in range(1, 120))
    assert len(paste) > 600
    messages = [
        {"role": "user", "content": paste},
        {"role": "user", "content": "最新的真实用户消息。"},
    ]
    users = _project(messages)
    projected = users[0]["content"]
    assert projected != paste, "a plain historical user paste should still be trimmed"
    assert len(projected) <= 600 + 64, "trimmed paste should sit around the regime-A budget"


def test_exempt_flag_off_restores_old_behavior():
    body = _wechat_notification_body()
    cfg = ProjectionConfig(user_msg_soft_trim_exempt_meta=False)
    messages = [
        {"role": "user", "content": body, "isMeta": True, "kind": "task_event"},
        {"role": "user", "content": "最新消息。"},
    ]
    out = cfg.expand_canonical_to_llm_messages(messages, format_metadata=_fmt)
    injected = [m for m in out if m.get("role") == "user"][0]["content"]
    assert injected != body and len(injected) <= 600 + 64, \
        "with the exemption off, isMeta reverts to being trimmed"


if __name__ == "__main__":
    test_ismeta_notification_survives_soft_trim()
    test_real_user_paste_still_trimmed()
    test_exempt_flag_off_restores_old_behavior()
    print("OK")
