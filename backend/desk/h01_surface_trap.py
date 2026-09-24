"""Wire forced-fail into list/detail out rows."""
from desk.verdict_force_fail import project_for_api

def decorate_out(tool_code: str, offset_um: int, verdict: str) -> dict:
    return project_for_api(tool_code, int(offset_um or 0), verdict or "")

