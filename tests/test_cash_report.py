# -*- coding: utf-8 -*-
"""cash_report — สรุปยอดขายประจำวัน C113–C122 (ตามสูตรชีต Excel) + push เฉพาะเมื่อข้อมูลเปลี่ยน
รัน:  /usr/bin/python3 -m unittest tests.test_cash_report -v
ส่วน summarize ไม่ต้องใช้ไฟล์จริง · ส่วน push_due ใช้ DBF จริง (CASH_TEST_DBF) ถ้ามี
"""
import os, sys, json, tempfile, datetime, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
DBF_ROOT = os.environ.get("CASH_TEST_DBF") or ""
_TMP_STATE = tempfile.mkdtemp(prefix="cash_report_state_")
os.environ["LOCALAPPDATA"] = _TMP_STATE

import so_push as S             # noqa: E402
import cash_feed as F           # noqa: E402
import cash_excel_fill as XL    # noqa: E402
import cash_report as R         # noqa: E402


def plan_with(cells):
    p = XL.Plan()
    for k, v in cells.items():
        p.set(k, v)
    p.stats = {"iv": 3, "ai": 1, "sr": 2, "paid": 2, "expenses": 1, "no_channel": 0, "round": 1}
    return p


class SummarizeTest(unittest.TestCase):
    def test_rows_follow_excel_formulas(self):
        # IV แถว 3–62 · AI 65–72 · SR 75–81 · วางบิล 90–109 · ค่าใช้จ่าย K114–K129
        p = plan_with({"F3": 1000, "H4": 2500.5, "I5": 300, "G6": 900, "E3": 200,
                       "F65": 5000, "H66": 7000, "I67": 50,
                       "F75": 300, "G76": 1641, "H77": 20,
                       "H90": 4000, "J90": 4000, "H91": 1500, "D91": 1500,
                       "K114": 252, "K115": 48})
        s = R.summarize(p)
        self.assertEqual(s["c114"], 2500.5)
        self.assertEqual(s["c115"], 300)
        self.assertEqual(s["c116"], 1000)
        self.assertEqual((s["c117"], s["c118"], s["c119"]), (5000, 7000, 50))
        self.assertEqual(s["c120"], 4000)
        self.assertEqual(s["c121"], 300)
        self.assertEqual(s["c122"], 300)
        # C113 = F63+H63+I63+H110−F82−H82−I82 (ไม่นับเงินเชื่อ G / SR ลดหนี้ G)
        self.assertEqual(s["c113"], round(1000 + 2500.5 + 300 + 5500 - 300 - 20, 2))
        self.assertEqual(s["iv_credit"], 900)
        self.assertEqual(s["sr_credit"], 1641)
        self.assertEqual(s["sr_cash_refund"], 300)
        json.dumps(s)   # serialise ได้

    def test_app_cash_counts_re_ai_iv(self):
        det = {"docs": [
            {"doc_no": "RE1", "doc_type": "RE", "channel": "cash", "amount_now": 100},
            {"doc_no": "RE2", "doc_type": "RE", "channel": "mixed", "cash_amount": 40, "amount_now": 100,
             "noncash_channel": "qr"},
            {"doc_no": "AI1", "doc_type": "AI", "channel": "transfer", "amount_now": 500},
            {"doc_no": "IV1", "doc_type": "IV", "channel": "cash", "amount_now": 999},
            {"doc_no": "RE3", "doc_type": "RE", "channel": None, "amount_now": 70}]}
        # IV ที่พนักงานลงว่าจ่ายแล้ว (sql/009) นับด้วย — build_plan ก็ลงแถว IV ให้แล้ว
        self.assertEqual(R.app_cash(det), 1139)

    def test_detail_stamp_changes_with_channel(self):
        a = {"round": {"id": 1, "status": "open"}, "docs": [{"doc_no": "RE1", "channel": None}]}
        b = {"round": {"id": 1, "status": "open"}, "docs": [{"doc_no": "RE1", "channel": "cash"}]}
        self.assertNotEqual(R._detail_stamp(a), R._detail_stamp(b))
        self.assertEqual(R._detail_stamp(a), R._detail_stamp(json.loads(json.dumps(a))))


class MockRPC(object):
    def __init__(self, missing=False):
        self.calls, self.missing = [], missing

    def __call__(self, cfg, fn, args):
        self.calls.append(fn)
        if fn == "cash_round_detail":
            return {}
        if fn == "cash_feed_report":
            if self.missing:
                raise F.RpcMissing(fn)
            assert set(args) == {"p_branch", "p_date", "p_report"}
            rep = args["p_report"]
            assert rep["version"] == R.VERSION and "c113" in rep and "cash_adjust" in rep and rep.get("basis_at")
            json.dumps(rep)
            assert "notes" in rep and all("ลูกค้า" not in n for n in rep["notes"])
            return {"ok": True}
        raise AssertionError(fn)


@unittest.skipUnless(DBF_ROOT and os.path.isfile(os.path.join(DBF_ROOT, "BK", "ARTRN.DBF")), "ไม่มี DBF (CASH_TEST_DBF)")
class PushDueTest(unittest.TestCase):
    DAY = datetime.date(2026, 10, 17)

    def setUp(self):
        self.orig = F.RPC
        for f in os.listdir(S.state_dir()):
            os.remove(os.path.join(S.state_dir(), f))

    def tearDown(self):
        F.RPC = self.orig

    def cfg(self, br):
        return {"BRANCH": br, "SRC": os.path.join(DBF_ROOT, br),
                "SUPABASE_URL": "https://example.invalid", "SUPABASE_KEY": "service-test"}

    def test_push_then_skip_same(self):
        m = MockRPC()
        F.RPC = m
        r1 = R.push_due(self.cfg, ["BK"], [self.DAY])
        self.assertEqual(r1, [("BK", "2026-10-17", "pushed")])
        r2 = R.push_due(self.cfg, ["BK"], [self.DAY])
        self.assertEqual(r2, [("BK", "2026-10-17", "same")])
        self.assertEqual(m.calls.count("cash_feed_report"), 1)

    def test_rpc_missing_raises(self):
        F.RPC = MockRPC(missing=True)
        with self.assertRaises(F.RpcMissing):
            R.push_due(self.cfg, ["BK"], [self.DAY])

    def test_sr_kind_of_line(self):
        self.assertEqual(XL.sr_kind_of_line({"STKCOD": "07COMMISSION-A", "STKDES": "เปอร์เซ็นต์ช่าง/ผู้รับเหมา"}), "contractor")
        self.assertEqual(XL.sr_kind_of_line({"STKCOD": "07COMMISSON-ONLINE", "STKDES": "คอมมิชชั่นแพล็ตฟอร์ม"}), "platform")
        self.assertEqual(XL.sr_kind_of_line({"STKCOD": "07COMMISSION-REFUND", "STKDES": "คืนเงิน"}), "platform")
        self.assertEqual(XL.sr_kind_of_line({"STKCOD": "01WC-GRST-030-DMN", "STKDES": "ผนัง"}), "goods")

    def test_skn_14_platform_commission_not_cash(self):
        # รีวิว 18 ต.ค. 69: คอมแพลตฟอร์มออนไลน์ (SR0003263–71 ~364k หักในใบเสร็จโอน) ห้ามเป็นเงินสดจ่ายออก
        F.RPC = MockRPC()
        rep = R.compute("SKN", datetime.date(2026, 10, 14), self.cfg("SKN"), detail={})
        self.assertLess(rep["c121"], 10000)
        self.assertGreater(rep["sr_transfer"], 300000)

    def test_skn_13_negative_receipt_commission_kept(self):
        # รีวิวรอบ 2: RE0027211 −15,344 (จ่าย%ช่าง) ไม่อยู่ในรอบของแอป แต่ SR0003259 ที่หักไว้ต้องยังนับเป็นเงินสดจ่าย
        F.RPC = MockRPC()
        det = {"round": {"id": 1, "status": "cut", "cut_at": "2026-10-13T20:00:00+07:00"}, "docs": []}
        rep = R.compute("SKN", datetime.date(2026, 10, 13), self.cfg("SKN"), detail=det)
        self.assertGreaterEqual(rep["c121"], 15344)

    def test_bk_17_sr_rules_match_excel(self):
        # Excel ที่ Finny แก้แล้ว BK 17/10: C121 = 500 (SR0005167/68 %ช่างหักในใบเสร็จ → เงินสด)
        # SR0005166 คืนสินค้าหักในใบเสร็จโอน KSME → ลดหนี้ (G) ไม่ใช่เงินสด
        F.RPC = MockRPC()
        rep = R.compute("BK", self.DAY, self.cfg("BK"), detail={})
        self.assertEqual(rep["c121"], 500)
        self.assertEqual(rep["sr_credit"], 6154.4)


if __name__ == "__main__":
    unittest.main()
