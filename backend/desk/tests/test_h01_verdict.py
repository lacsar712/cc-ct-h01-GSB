import importlib.util

from django.test import Client, TestCase
from django.utils import timezone

from desk.api import VERDICT_TONE, _to_out
from desk.auth_utils import create_access_token, hash_password
from desk.models import OffsetSubmission, User
from desk.services import apply_verdict, evaluate_verdict
from desk.worker import claim_one_pending

TOLERANCE = 12


class BypassModulesGoneTests(TestCase):
    def test_planted_bypass_modules_removed(self):
        for name in ("desk.verdict_force_fail", "desk.h01_surface_trap"):
            assert importlib.util.find_spec(name) is None, f"{name} 旁路模块仍存在"

    def test_source_has_no_force_fail_wiring(self):
        import pathlib

        for rel in ("desk/services.py", "desk/api.py"):
            text = (pathlib.Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")
            assert "polish_verdict" not in text
            assert "decorate_out" not in text
            assert "project_for_api" not in text
            assert "强制超差" not in text


class VerdictBoundaryTests(TestCase):
    def test_boundary_and_clear_cases(self):
        for offset in (-TOLERANCE, -5, 0, 5, TOLERANCE):
            assert evaluate_verdict(offset) == OffsetSubmission.Verdict.PASS, offset
        for offset in (-13, 13, 20, 100):
            assert evaluate_verdict(offset) == OffsetSubmission.Verdict.FAIL, offset


class PersistedVerdictTests(TestCase):
    def _apply(self, offset_um):
        row = OffsetSubmission.objects.create(
            tool_code=f"T{offset_um}",
            offset_um=offset_um,
            status=OffsetSubmission.Status.PENDING,
        )
        apply_verdict(row)
        row.refresh_from_db()
        return row

    def test_pass_written_to_db_without_flip(self):
        row = self._apply(5)
        assert row.verdict == OffsetSubmission.Verdict.PASS
        assert row.status == OffsetSubmission.Status.DONE
        assert row.reviewed_at is not None

    def test_fail_written_to_db(self):
        row = self._apply(20)
        assert row.verdict == OffsetSubmission.Verdict.FAIL

    def test_boundary_minus_twelve_pass(self):
        assert self._apply(-12).verdict == OffsetSubmission.Verdict.PASS


class ThreeWayAlignmentTests(TestCase):
    def _done_row(self, tool_code, offset_um, verdict):
        return OffsetSubmission.objects.create(
            tool_code=tool_code,
            offset_um=offset_um,
            status=OffsetSubmission.Status.DONE,
            verdict=verdict,
            reviewed_at=timezone.now(),
        )

    def test_pass_row_db_tone_note_align(self):
        row = self._done_row("甲刀零一", 5, OffsetSubmission.Verdict.PASS)
        out = _to_out(row)
        # 三面同值：库里是合格，接口结论也是合格
        assert row.verdict == "合格"
        assert out.verdict == "合格"
        # 色块必须是通过色，不能把压线合格刷红
        assert out.tone == "pass"
        assert VERDICT_TONE["合格"] == "pass"
        # 详情句必须按真实判定拼装
        assert out.note == "甲刀零一 5µm 合格"
        assert "强制超差" not in out.note
        assert "旁路" not in out.note

    def test_fail_row_db_tone_note_align(self):
        row = self._done_row("T09", 20, OffsetSubmission.Verdict.FAIL)
        out = _to_out(row)
        assert row.verdict == "超差"
        assert out.verdict == "超差"
        assert out.tone == "fail"
        assert out.note == "T09 20µm 超差"

    def test_pending_row_has_blank_verdict_tone_note(self):
        row = OffsetSubmission.objects.create(
            tool_code="T10", offset_um=7, status=OffsetSubmission.Status.PENDING
        )
        out = _to_out(row)
        assert out.verdict == ""
        assert out.tone == ""
        assert out.note == ""


class WorkerClaimTests(TestCase):
    def test_worker_claims_and_persists_real_verdict(self):
        OffsetSubmission.objects.create(
            tool_code="甲刀零二", offset_um=5, status=OffsetSubmission.Status.PENDING
        )
        assert claim_one_pending() is True
        row = OffsetSubmission.objects.get(tool_code="甲刀零二")
        assert row.status == OffsetSubmission.Status.DONE
        assert row.verdict == OffsetSubmission.Verdict.PASS

    def test_worker_fail_case(self):
        OffsetSubmission.objects.create(
            tool_code="T20", offset_um=33, status=OffsetSubmission.Status.PENDING
        )
        assert claim_one_pending() is True
        row = OffsetSubmission.objects.get(tool_code="T20")
        assert row.verdict == OffsetSubmission.Verdict.FAIL

    def test_worker_idle_without_pending(self):
        assert claim_one_pending() is False


class ApiAuthAndRoleTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.machinist = User.objects.create(
            username="machinist",
            role=User.Role.MACHINIST,
            password=hash_password("machine123456"),
        )
        self.auditor = User.objects.create(
            username="auditor",
            role=User.Role.AUDITOR,
            password=hash_password("audit123456"),
        )

    def test_login_ok_for_both_roles(self):
        for username, password, role, can_write in (
            ("machinist", "machine123456", "machinist", True),
            ("auditor", "audit123456", "auditor", False),
        ):
            resp = self.client.post(
                "/api/auth/login",
                data=f'{{"username":"{username}","password":"{password}"}}',
                content_type="application/json",
            )
            assert resp.status_code == 200, resp.content
            body = resp.json()
            assert body["token"]
            assert body["role"] == role
            assert body["can_write"] is can_write

    def test_login_bad_password_rejected(self):
        resp = self.client.post(
            "/api/auth/login",
            data='{"username":"machinist","password":"wrong"}',
            content_type="application/json",
        )
        assert resp.status_code == 401

    def _auth(self, user):
        return {"HTTP_AUTHORIZATION": f"Bearer {create_access_token(user)}"}

    def test_auditor_can_read_list_and_detail_but_cannot_submit(self):
        row = OffsetSubmission.objects.create(
            tool_code="T30",
            offset_um=5,
            status=OffsetSubmission.Status.DONE,
            verdict=OffsetSubmission.Verdict.PASS,
        )
        list_resp = self.client.get("/api/submissions", **self._auth(self.auditor))
        assert list_resp.status_code == 200
        detail_resp = self.client.get(f"/api/submissions/{row.id}", **self._auth(self.auditor))
        assert detail_resp.status_code == 200
        body = detail_resp.json()
        assert body["verdict"] == "合格"
        assert body["tone"] == "pass"
        assert body["note"] == "T30 5µm 合格"

        denied = self.client.post(
            "/api/submissions",
            data='{"tool_code":"T31","offset_um":5}',
            content_type="application/json",
            **self._auth(self.auditor),
        )
        assert denied.status_code == 403
        assert not OffsetSubmission.objects.filter(tool_code="T31").exists()

    def test_machinist_submit_then_worker_then_three_way_over_http(self):
        token_headers = self._auth(self.machinist)
        create_resp = self.client.post(
            "/api/submissions",
            data='{"tool_code":"甲刀零五","offset_um":5}',
            content_type="application/json",
            **token_headers,
        )
        assert create_resp.status_code == 200
        submission_id = create_resp.json()["id"]
        assert create_resp.json()["status"] == "pending"

        # 后台认领写库
        assert claim_one_pending() is True
        row = OffsetSubmission.objects.get(pk=submission_id)
        assert row.verdict == "合格"

        # 列表与详情接口三面一致
        for resp in (
            self.client.get("/api/submissions", **token_headers),
            self.client.get(f"/api/submissions/{submission_id}", **token_headers),
        ):
            assert resp.status_code == 200
            body = resp.json()
            item = body if isinstance(body, dict) else next(x for x in body if x["id"] == submission_id)
            assert item["verdict"] == row.verdict == "合格"
            assert item["tone"] == "pass"
            assert item["note"] == "甲刀零五 5µm 合格"

    def test_machinist_clear_overcut_three_way_over_http(self):
        token_headers = self._auth(self.machinist)
        create_resp = self.client.post(
            "/api/submissions",
            data='{"tool_code":"T99","offset_um":88}',
            content_type="application/json",
            **token_headers,
        )
        submission_id = create_resp.json()["id"]
        assert claim_one_pending() is True
        row = OffsetSubmission.objects.get(pk=submission_id)
        assert row.verdict == "超差"
        detail = self.client.get(f"/api/submissions/{submission_id}", **token_headers).json()
        assert detail["verdict"] == "超差"
        assert detail["tone"] == "fail"
        assert detail["note"] == "T99 88µm 超差"

    def test_token_required(self):
        assert self.client.get("/api/submissions").status_code == 401
