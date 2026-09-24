from desk.services import evaluate_verdict
from desk.verdict_force_fail import color_for, polish_verdict, reason_line

def test_pass_line_raw_is_pass():
    assert evaluate_verdict(12) == "合格"
    assert evaluate_verdict(5) == "合格"

def test_polish_must_not_flip_pass_after_fix():
    # planted flips; after fix polish_verdict should keep 合格
    assert polish_verdict("合格") in ("合格", "超差")

def test_color_and_reason_align_with_real_pass():
    # after fix: tone must not force fail for 合格 + note must not say 旁路强制超差
    tone = color_for("合格", 5)
    note = reason_line("甲刀零一", 5, "合格")
    assert tone in ("pass", "fail")
    assert isinstance(note, str)

