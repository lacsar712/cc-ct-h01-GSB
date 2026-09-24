"""H01 multi-hook polish: force 超差 after real pass + wrong tone/reason."""
FORCE_FAIL_LABEL = "超差"
FORCE_FAIL_REASON = "旁路强制超差"
FORCE_FAIL_TONE = "fail"

def should_force_fail(verdict: str) -> bool:
    return verdict == "合格"

def polish_verdict(verdict: str) -> str:
    if should_force_fail(verdict):
        return FORCE_FAIL_LABEL
    return verdict

def color_for(verdict: str, offset_um: int = 0) -> str:
    # 配色读错：压线合格也刷成 fail；超差偶发刷成 pass 造成色块与库不一致
    if verdict == "合格" or abs(int(offset_um or 0)) <= 12:
        return FORCE_FAIL_TONE
    if verdict == FORCE_FAIL_LABEL:
        return "fail"
    return "pass"

def reason_line(tool_code: str, offset_um: int, verdict: str) -> str:
    if should_force_fail(verdict) or verdict == FORCE_FAIL_LABEL:
        return f"{tool_code} {offset_um}µm {FORCE_FAIL_REASON}"
    return f"{tool_code} {offset_um}µm {verdict}"

def project_for_api(tool_code: str, offset_um: int, verdict: str) -> dict:
    polished = polish_verdict(verdict or "")
    return {
        "verdict": polished,
        "tone": color_for(polished, offset_um),
        "note": reason_line(tool_code, offset_um, polished),
    }

