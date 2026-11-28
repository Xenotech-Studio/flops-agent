import pytest

from flops_agent.engine.compaction import abbreviate_repeated_summary_lines, detect_repeated_lines


@pytest.mark.parametrize("count", [2, 3, 4])
@pytest.mark.parametrize(("line", "preview"), [
    ("one two THREE four five six seven eight nine ten eleven twelve",
     "one two THREE four five six seven eight nine ten"),
    ("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥" * 2, "甲乙丙丁戊己庚辛壬癸子丑寅卯辰"),
    ("sample 🚀 中文与English混排测试内容" * 2, "sample 🚀 中文与Eng"),
    ("日本語のひらがなとカタカナを含むサンプルです。" * 2, "日本語のひらがなとカタカナを含"),
    ("ภาษาไทยทดสอบการตัดข้อความซ้ำ" * 2, "ภาษาไทยทดสอบการ"),
])
def test_abbreviate_exact_long_summary_lines(count, line, preview):
    originals = [f"  {line}\t\r", line] + [f"\t{line} " for _ in range(count - 2)]
    untouched = ["", "short", "short", "short", f" {line} different  ", ""]
    summary = "\n".join(originals + untouched)
    expected = "\n".join(originals[:2] + [f"（repeat：“{preview}...”）"] * (count - 2) + untouched)
    assert detect_repeated_lines(summary) == (line if count >= 3 else None)
    assert abbreviate_repeated_summary_lines(summary) == expected


def test_repetition_threshold_also_applies_at_thirty_characters():
    short = "甲" * 29
    long = "乙" * 30
    summary = "\n".join([short, long] * 3)
    expected = "\n".join([short, long] * 2 + [short, f"（repeat：“{'乙' * 15}...”）"])
    assert abbreviate_repeated_summary_lines(summary) == expected
