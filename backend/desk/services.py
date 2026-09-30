from django.conf import settings
from django.utils import timezone

from desk.models import OffsetSubmission


def evaluate_verdict(offset_um: int) -> str:
    if abs(offset_um) <= settings.OFFSET_TOLERANCE_UM:
        return OffsetSubmission.Verdict.PASS
    return OffsetSubmission.Verdict.FAIL


def apply_verdict(submission: OffsetSubmission) -> None:
    submission.verdict = evaluate_verdict(submission.offset_um)
    submission.status = OffsetSubmission.Status.DONE
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=["verdict", "status", "reviewed_at"],
    )


def tone_for(verdict: str) -> str:
    """色块只由库中真实结论决定，不得另立判据。"""
    if verdict == OffsetSubmission.Verdict.PASS:
        return "pass"
    if verdict == OffsetSubmission.Verdict.FAIL:
        return "fail"
    return ""


def reason_line(tool_code: str, offset_um: int, verdict: str) -> str:
    """详情说明句必须与真实结论同词。"""
    if verdict:
        return f"{tool_code} {offset_um}µm {verdict}"
    return ""
