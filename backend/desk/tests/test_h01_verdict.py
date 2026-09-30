"""H01 验收：库结论 / 色块 tone / 详情 note 三面对拍，且不允许任何旁路翻转。"""

import json

from django.test import Client, TestCase

from desk.api import _to_out
from desk.auth_utils import hash_password
from desk.models import OffsetSubmission, User
from desk.services import apply_verdict, evaluate_verdict, reason_line, tone_for
from desk.worker import claim_one_pending


PASS = OffsetSubmission.Verdict.PASS  # 合格
FAIL = OffsetSubmission.Verdict.FAIL  # 超差


def _make_user(username, role, password="pw123456"):
    return User.objects.create(
        username=username,
        role=role,
        password=hash_password(password),
    )


def _make_submission(tool_code, offset_um, user, status=OffsetSubmission.Status.PENDING):
    return OffsetSubmission.objects.create(
        tool_code=tool_code,
        offset_um=offset_um,
        submitted_by=user,
        status=status,
    )


def _assert_three_faces(row, verdict):
    """库结论、API 出口的 verdict/tone/note 必须三面同值。"""
    row.refresh_from_db()
    assert row.verdict == verdict

    out = _to_out(row)
    assert out.verdict == verdict
    assert out.tone == tone_for(verdict)
    assert out.note == reason_line(row.tool_code, row.offset_um, verdict)
    assert verdict in out.note
    if verdict == PASS:
        assert out.tone == "pass"
        assert FAIL not in out.note
    else:
        assert out.tone == "fail"
        assert PASS not in out.note


class VerdictTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.machinist = _make_user("machinist", User.Role.MACHINIST)
        cls.auditor = _make_user("auditor", User.Role.AUDITOR)

    # ---- 判定函数边界 ----
    def test_evaluate_boundary(self):
        for value in (-12, -5, 0, 5, 12):
            assert evaluate_verdict(value) == PASS, value
        for value in (-13, -20, 13, 20):
            assert evaluate_verdict(value) == FAIL, value

    # ---- 第一处：写库前不得篡改 ----
    def test_apply_verdict_writes_real_verdict_to_db(self):
        row = _make_submission("甲刀", 5, self.machinist)
        apply_verdict(row)
        row.refresh_from_db()
        assert row.verdict == PASS
        assert row.status == OffsetSubmission.Status.DONE
        assert row.reviewed_at is not None

    def test_worker_claim_writes_real_verdict(self):
        row = _make_submission("甲刀", 5, self.machinist)
        assert claim_one_pending() is True
        row.refresh_from_db()
        assert row.status == OffsetSubmission.Status.DONE
        assert row.verdict == PASS

    # ---- 三面对拍：压线/边界合格 ----
    def test_pass_row_three_faces_agree(self):
        row = _make_submission("甲刀", 5, self.machinist)
        apply_verdict(row)
        _assert_three_faces(row, PASS)

    def test_boundary_12_row_three_faces_agree(self):
        row = _make_submission("甲刀", 12, self.machinist)
        apply_verdict(row)
        _assert_three_faces(row, PASS)

    # ---- 三面对拍：一笔明显超差 ----
    def test_fail_row_three_faces_agree(self):
        row = _make_submission("乙刀", 20, self.machinist)
        apply_verdict(row)
        _assert_three_faces(row, FAIL)

    def test_pending_row_has_no_verdict_face(self):
        row = _make_submission("甲刀", 5, self.machinist)
        out = _to_out(row)
        assert out.verdict == ""
        assert out.tone == ""
        assert out.note == ""


class ApiFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.machinist = _make_user(
            "machinist", User.Role.MACHINIST, "machine123456"
        )
        cls.auditor = _make_user("auditor", User.Role.AUDITOR, "audit123456")

    def setUp(self):
        self.client = Client()

    def _login(self, username, password):
        resp = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.content
        return resp.json()["token"]

    def _auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def _submit_and_process(self, token, tool_code, offset_um):
        resp = self.client.post(
            "/api/submissions",
            data=json.dumps({"tool_code": tool_code, "offset_um": offset_um}),
            content_type="application/json",
            **self._auth(token),
        )
        assert resp.status_code == 200, resp.content
        created = resp.json()
        assert created["status"] == "pending"
        assert created["verdict"] == ""
        assert claim_one_pending() is True
        return created["id"]

    def _assert_api_three_faces(self, token, sid, verdict, tone, tool_code, offset_um):
        # 总览列表
        resp = self.client.get("/api/submissions", **self._auth(token))
        assert resp.status_code == 200
        listed = {r["id"]: r for r in resp.json()}
        row = listed[sid]
        assert row["verdict"] == verdict
        assert row["tone"] == tone
        assert row["note"] == f"{tool_code} {offset_um}µm {verdict}"

        # 详情页
        resp = self.client.get(f"/api/submissions/{sid}", **self._auth(token))
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["verdict"] == row["verdict"] == verdict
        assert detail["tone"] == row["tone"] == tone
        assert detail["note"] == row["note"]

        # 库结论
        assert OffsetSubmission.objects.get(pk=sid).verdict == verdict

    def test_login_stays_and_pass_row_three_faces(self):
        token = self._login("machinist", "machine123456")
        sid = self._submit_and_process(token, "甲刀", 5)
        # 连续翻页访问不掉登录
        for _ in range(3):
            assert self.client.get("/api/submissions", **self._auth(token)).status_code == 200
            assert self.client.get(f"/api/submissions/{sid}", **self._auth(token)).status_code == 200
        self._assert_api_three_faces(token, sid, PASS, "pass", "甲刀", 5)

    def test_obviously_over_tolerance_row_three_faces(self):
        token = self._login("machinist", "machine123456")
        sid = self._submit_and_process(token, "乙刀", 20)
        self._assert_api_three_faces(token, sid, FAIL, "fail", "乙刀", 20)

    def test_auditor_login_works_reads_pages_but_cannot_submit(self):
        token = self._login("auditor", "audit123456")
        # 复核员能登录、能继续翻页（列表/详情）
        assert self.client.get("/api/submissions", **self._auth(token)).status_code == 200
        done = OffsetSubmission.objects.create(
            tool_code="甲刀",
            offset_um=5,
            submitted_by=self.machinist,
            status=OffsetSubmission.Status.DONE,
            verdict=PASS,
        )
        detail = self.client.get(f"/api/submissions/{done.id}", **self._auth(token))
        assert detail.status_code == 200
        assert detail.json()["verdict"] == PASS

        # 复核员仍然交不进刀补
        resp = self.client.post(
            "/api/submissions",
            data=json.dumps({"tool_code": "丙刀", "offset_um": 5}),
            content_type="application/json",
            **self._auth(token),
        )
        assert resp.status_code == 403
        assert not OffsetSubmission.objects.filter(tool_code="丙刀").exists()
