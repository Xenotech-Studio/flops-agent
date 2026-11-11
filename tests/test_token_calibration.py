"""Measured GLM prose and character categories guard against late compaction."""
import pytest

from flops_agent.engine.compaction import ProjectionConfig


@pytest.mark.parametrize('text,old,new', [
    ('中' * 1000, 677, 834),
    ('a' * 1000, 288, 313),
    ('中文text' * 1000, 2503, 2917),
    (' x' * 1000, 575, 625),
    ('😀' * 1000, 288, 313),
    ('\U00020000' * 1000, 288, 313),
])
def test_category_regression_table(text, old, new):
    estimate = ProjectionConfig().estimate_tokens(text)
    assert estimate == new
    assert estimate > old


def test_measured_chinese_prose_is_conservatively_budgeted():
    unit = ('我们正在设计一个支持长期对话的智能助手。用户希望系统准确保留项目目标、技术约束和已经确认的决定。'
            '当历史消息接近上下文窗口时，需要先生成中文摘要，再继续回答问题。摘要应记录待办事项和验证结果，避免重复无关内容。')
    config = ProjectionConfig()
    for chars, usage in [(1000, 613), (10000, 5979), (100000, 59632)]:
        text = (unit * ((chars + len(unit) - 1) // len(unit)))[:chars]
        assert usage <= config.estimate_tokens(text) <= usage * 1.45


def test_empty_images_and_explicit_configuration_remain_supported():
    config = ProjectionConfig()
    assert config.estimate_tokens('') == 0
    assert config.image_weight(2, 'token') == 3000
    assert ProjectionConfig(cjk_chars_per_token=2, latin_chars_per_token=4,
                            safety_margin=1, global_calibration=2).estimate_tokens('中文abcd') == 4
