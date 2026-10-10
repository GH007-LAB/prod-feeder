# -*- coding: utf-8 -*-
"""เทสต์ cash_feed / cash_poller ด้วย DBF จริง (สำเนา) + mock RPC — ไม่แตะ Supabase / ~/007so_push

รัน:  CASH_TEST_DBF=<โฟลเดอร์ที่มี BK/SKN/PPS> /usr/bin/python3 -m unittest tests.test_cash_feed -v
      (ไม่ตั้ง = ใช้สำเนา 5 ต.ค. 69 ใน scratchpad ของเซสชันที่เขียนเทสต์ · ไม่มีไฟล์ = skip)
"""
import os, sys, json, shutil, tempfile, datetime, unittest, threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

DBF_ROOT = os.environ.get("CASH_TEST_DBF") or (
    "/private/tmp/claude-501/-Users-cto007/8f158c1c-f2fb-4fa7-a80a-45c4ffa17bd4/scratchpad/dbf")
DAY = datetime.date(2026, 10, 2)

# state_dir() ใช้ LOCALAPPDATA ก่อน HOME — ชี้ไป temp กันเขียนทับ state จริง
_TMP_STATE = tempfile.mkdtemp(prefix="cash_test_state_")
os.environ["LOCALAPPDATA"] = _TMP_STATE

import so_push as S      # noqa: E402
import cash_feed as F    # noqa: E402
import cash_poller as P  # noqa: E402

DOC_KEYS = {"doc_no", "doc_type", "doc_date", "customer_code", "customer_name", "amount", "docstat",
            "iv_refs"}  # RE→IV จาก ARRCPIT (9 ต.ค. 69)
IV_KEYS = {"doc_no", "doc_date", "customer_code", "customer_name", "amount"}
OUT_KEYS = {"branch", "date", "cutoff_at", "cutoff_by", "float", "submitted_at", "preparer", "docs",
            "expenses", "cash_counted", "summary", "doc_count_at_cutoff"}


class MockRPC(object):
    """จำลอง RPC ฝั่ง feeder + ตรวจ payload ตามเงื่อนไขเดียวกับ 002_cash_rpc.sql"""

    def __init__(self, tick=None, fail=None):
        self.calls = []
        self.tick = tick or {}
        self.fail = fail or set()
        self.extra_ids = set()

    def __call__(self, cfg, fn, args):
        assert cfg["SUPABASE_KEY"] == "service-test", "ต้องใช้ service key"
        self.calls.append((fn, json.loads(json.dumps(args))))   # ต้อง serialise ได้
        if fn in self.fail:
            raise RuntimeError("mock fail " + fn)
        if fn == "cash_feed_docs":
            docs = args["p_docs"]
            assert isinstance(docs, list)
            assert set(args) <= {"p_branch", "p_docs", "p_window_from", "p_dbf_mtime"}, args
            if args.get("p_dbf_mtime") is not None:
                datetime.datetime.fromisoformat(args["p_dbf_mtime"])
            if args.get("p_window_from") is not None:
                assert docs, "window + payload ว่าง = SQL ปฏิเสธ"
                datetime.date.fromisoformat(args["p_window_from"])
            nos = [d["doc_no"] for d in docs]
            assert len(nos) == len(set(nos)), "doc_no ซ้ำ"
            for d in docs:
                assert set(d) == DOC_KEYS, d   # ยอด ≤ 0 ส่งได้ (contract ใหม่)
                assert d["doc_type"] in ("RE", "AI"), d
                datetime.date.fromisoformat(d["doc_date"])
                assert isinstance(d["amount"], float) and d["doc_no"]
            return {"branch": args["p_branch"], "received": len(docs), "newly_flagged": 0,
                    "newly_missing": 0}
        if fn == "cash_feed_unpaid_iv":
            for r in args["p_rows"]:
                assert set(r) == IV_KEYS and r["doc_no"].startswith("IV") and r["amount"] > 0, r
            return {"branch": args["p_branch"], "rows": len(args["p_rows"])}
        if fn == "cash_tick":
            return self.tick
        if fn == "cash_finalize_cut":
            extra = args["p_round_id"] in self.extra_ids
            return {"round_id": args["p_round_id"], "status": "cut", "cut_by": "button",
                    "extra_pull": extra, "added": 2 if extra else 3,
                    "doc_count": 5 if extra else 3, "carried_count": 1}
        if fn == "cash_export_out":
            return {"branch": "SKN", "date": "2026-10-02", "cutoff_at": "2026-10-02T16:41:00+07:00",
                    "cutoff_by": "button", "float": 5000.0, "submitted_at": "2026-10-02T16:50:00+07:00",
                    "preparer": "นัท", "docs": [{"doc_no": "RE0027100", "doc_date": "2026-10-02",
                                                "type": "RE", "total": 20635.0, "carried": False,
                                                "channel": "cash", "cash_amount": 20635.0,
                                                "changed_after_cutoff": False}],
                    "expenses": [], "cash_counted": 25635.0,
                    "summary": {"cash_in": 20635.0, "transfer_in": 0, "cash_refund": 0,
                                "cash_expense": 0, "cash_expected": 25635.0, "diff": 0, "deposit": 20635.0},
                    "doc_count_at_cutoff": 1}
        if fn == "cash_mark_exported":
            return None
        if fn == "cash_purge_old":
            assert args == {}, args
            return {"before": "2025-09-05", "cash_doc": 4, "cash_unpaid_iv": 0}
        raise AssertionError("RPC ไม่รู้จัก " + fn)

    def names(self):
        return [c[0] for c in self.calls]


def cfg_for(br, src=None):
    return {"BRANCH": br, "SRC": src or os.path.join(DBF_ROOT, br),
            "SUPABASE_URL": "https://example.invalid", "SUPABASE_KEY": "service-test"}


@unittest.skipUnless(os.path.isfile(os.path.join(DBF_ROOT, "BK", "ARTRN.DBF")), "ไม่มีสำเนา DBF")
class CashFeedTest(unittest.TestCase):
    def setUp(self):
        for f in os.listdir(_TMP_STATE):
            p = os.path.join(_TMP_STATE, f)
            if os.path.isfile(p):
                os.remove(p)
        os.makedirs(os.path.join(_TMP_STATE, "007so_push"), exist_ok=True)
        for f in os.listdir(S.state_dir()):
            os.remove(os.path.join(S.state_dir(), f))
        S.PROXY.clear()
        F.clear_cache()

    # ---------- ตัวเลขของวันที่ 2026-10-02 (นับมือจาก DBF ตอนสำรวจ) ----------
    def test_counts_2026_10_02(self):
        # RE / AI / IV ค้าง ที่ doc_date = 2 ต.ค. ต่อสาขา
        expect = {"BK": (13, 1, 1), "SKN": (15, 1, 8), "PPS": (9, 0, 0)}
        for br, (n_re, n_ai, n_iv) in expect.items():
            F.clear_cache()
            docs, unpaid, info = F.collect(cfg_for(br)["SRC"], br, DAY)
            day = [d for d in docs if d["doc_date"] == DAY.isoformat()]
            self.assertEqual(sum(d["doc_type"] == "RE" for d in day), n_re, br)
            self.assertEqual(sum(d["doc_type"] == "AI" for d in day), n_ai, br)
            self.assertEqual(sum(r["doc_date"] == DAY.isoformat() for r in unpaid), n_iv, br)
            self.assertTrue(info["complete"])
            self.assertEqual(info["window_from"], "2026-09-25")
            # ทุกใบใน payload อยู่ในช่วง (DOCDAT หรือ CHGDAT) และไม่มี IV/HS/SR หลุดมา
            self.assertTrue(all(d["doc_no"][:2] in ("RE", "AI") for d in docs), br)
            # ชื่อลูกค้าเติมจาก ARMAS + ไม่มี NBSP
            self.assertTrue(all("\xa0" not in (d["customer_name"] or "") for d in docs))
            self.assertGreater(sum(1 for d in day if d["customer_name"]), len(day) * 0.8, br)
            # ยอดรวมสมเหตุสมผล (บวก ไม่เกิน 1 ล้าน/วัน/สาขา)
            tot = sum(d["amount"] for d in day)
            self.assertTrue(0 < tot < 1e6, (br, tot))

    def test_known_docs(self):
        docs, unpaid, _ = F.collect(cfg_for("BK")["SRC"], "BK", DAY)
        by = {d["doc_no"]: d for d in docs}
        self.assertEqual(by["AI6900160"]["doc_type"], "AI")
        self.assertEqual(by["AI6900160"]["amount"], 10000.0)
        self.assertEqual(by["RE0061774"]["amount"], 16982.0)
        self.assertEqual(by["RE0061774"]["docstat"], "M")
        iv = {r["doc_no"]: r for r in unpaid}
        self.assertEqual(iv["IV6904843"]["amount"], 14991.0)
        self.assertNotIn("IV6904842", iv)     # ออก RE วันเดียวกันแล้ว REMAMT=0

    # ---------- sync + payload + fingerprint ----------
    def test_sync_payload_and_fingerprint(self):
        m = MockRPC()
        F.RPC = m
        r1 = F.sync_branch(cfg_for("SKN"), today=DAY)
        self.assertEqual(m.names(), ["cash_feed_docs", "cash_feed_unpaid_iv"])
        args = m.calls[0][1]
        self.assertEqual(args["p_branch"], "SKN")
        self.assertEqual(args["p_window_from"], "2026-09-25")
        # mtime ของ ARTRN ในโฟลเดอร์ต้นทาง (+07:00)
        exp = datetime.datetime.fromtimestamp(
            os.path.getmtime(os.path.join(DBF_ROOT, "SKN", "ARTRN.DBF")), F.TH).isoformat(timespec="seconds")
        self.assertEqual(args["p_dbf_mtime"], exp)
        self.assertTrue(r1["sent_docs"] and r1["sent_iv"])
        # รอบสอง ข้อมูลเดิม → ไม่ยิง
        m.calls.clear()
        r2 = F.sync_branch(cfg_for("SKN"), today=DAY)
        self.assertEqual(m.calls, [])
        self.assertFalse(r2["sent_docs"] or r2["sent_iv"])
        # force (ตอนตัดรอบ) ไม่โหลด IV → ยิงแค่ docs
        r3 = F.sync_branch(cfg_for("SKN"), today=DAY, force=True, with_unpaid=False)
        self.assertEqual(m.names(), ["cash_feed_docs"])
        self.assertIsNone(r3["unpaid"])

    def test_rpc_failure_does_not_save_state(self):
        F.RPC = MockRPC(fail={"cash_feed_docs"})
        with self.assertRaises(RuntimeError):
            F.sync_branch(cfg_for("PPS"), today=DAY)
        m = MockRPC()
        F.RPC = m
        F.sync_branch(cfg_for("PPS"), today=DAY)
        self.assertIn("cash_feed_docs", m.names())   # รอบหน้ายิงใหม่ ไม่โดน fingerprint กลืน

    def test_truncated_dbf_drops_window(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(DBF_ROOT, "BK")
            for f in ("ARMAS.DBF", "ARRCPIT.DBF"):
                shutil.copy(os.path.join(src, f), tmp)
            with open(os.path.join(src, "ARTRN.DBF"), "rb") as f:
                data = f.read()
            with open(os.path.join(tmp, "ARTRN.DBF"), "wb") as f:
                f.write(data[:len(data) - 5000])
            m = MockRPC()
            F.RPC = m
            F.sync_branch(cfg_for("BK", tmp), today=DAY)
            self.assertNotIn("p_window_from", m.calls[0][1])
            self.assertNotIn("cash_feed_unpaid_iv", m.names())   # แทนที่ทั้งชุดจากไฟล์ขาด = ลบของจริง
        finally:
            shutil.rmtree(tmp)

    def test_drop_guard(self):
        m = MockRPC()
        F.RPC = m
        F.sync_branch(cfg_for("BK"), today=DAY)
        st = os.path.join(S.state_dir(), "state_cash_BK.json")
        with open(st) as f:
            s = json.load(f)
        s["in_window"] += 100      # จำลองรอบก่อนเห็นใบมากกว่านี้เยอะ
        s["docs_fp"] = "x"
        with open(st, "w") as f:
            json.dump(s, f)
        m.calls.clear()
        F.sync_branch(cfg_for("BK"), today=DAY)
        # contract ใหม่: ไฟล์ครบ = ส่ง window เสมอ (ยกเลิกใน Express = ลบ record) แค่ลง ERROR เตือน
        self.assertEqual(m.calls[0][1]["p_window_from"], "2026-09-25")

    def test_nonpositive_amount_sent(self):
        docs, _, _ = F.collect(cfg_for("BK")["SRC"], "BK", datetime.date(2026, 9, 10), window_days=14)
        self.assertTrue(any(d["amount"] < 0 for d in docs), "BK มี RE ติดลบช่วงต้น ก.ย.")
        F.RPC = MockRPC()
        F.sync_branch(cfg_for("BK"), today=datetime.date(2026, 9, 10))   # mock ไม่ปฏิเสธยอด ≤ 0

    def test_mtime_changes_fingerprint(self):
        tmp = tempfile.mkdtemp()
        try:
            for f in ("ARTRN.DBF", "ARMAS.DBF", "ARRCPIT.DBF"):
                shutil.copy2(os.path.join(DBF_ROOT, "PPS", f), tmp)
            m = MockRPC()
            F.RPC = m
            F.sync_branch(cfg_for("PPS", tmp), today=DAY)
            m.calls.clear()
            F.sync_branch(cfg_for("PPS", tmp), today=DAY)
            self.assertEqual(m.calls, [])
            os.utime(os.path.join(tmp, "ARTRN.DBF"))       # Drive ได้ไฟล์ใหม่ เนื้อเดิม
            F.sync_branch(cfg_for("PPS", tmp), today=DAY)
            self.assertEqual(m.names(), ["cash_feed_docs"])  # อัปเดตความสด, IV ไม่ต้องยิงซ้ำ
        finally:
            shutil.rmtree(tmp)

    def test_rpc_missing_is_quiet(self):
        def missing(cfg, fn, args):
            raise F.RpcMissing(fn)
        F.RPC = missing
        with self.assertRaises(F.RpcMissing):
            F.sync_branch(cfg_for("BK"), today=DAY)

    def test_armas_cache_reused(self):
        F.RPC = MockRPC()
        F.sync_branch(cfg_for("BK"), today=DAY)
        cache = os.path.join(S.state_dir(), "cash_armas_BK.json")
        self.assertTrue(os.path.exists(cache))
        F.clear_cache()
        F.collect(cfg_for("BK")["SRC"], "BK", DAY)
        self.assertFalse(any(p.endswith("ARMAS.DBF") for p in F._BUF), "ควรใช้ cache ไม่อ่าน ARMAS")

    def test_armas_cache_mode_600(self):
        cache = os.path.join(S.state_dir(), "cash_armas_BK.json")
        with open(cache + ".tmp", "w") as f:       # tmp ค้างสิทธิ์กว้าง
            f.write("{}")
        os.chmod(cache + ".tmp", 0o644)
        F.load_names(cfg_for("BK")["SRC"], "BK", {"X"})
        self.assertEqual(os.stat(cache).st_mode & 0o777, 0o600)

    def test_purge_monthly(self):
        m = MockRPC()
        F.RPC = m
        cfg = cfg_for("BK")
        self.assertEqual(P.maybe_purge(cfg)["cash_doc"], 4)
        self.assertIsNone(P.maybe_purge(cfg))                       # เดือนนี้ทำแล้ว
        self.assertEqual(m.names().count("cash_purge_old"), 1)
        later = __import__("time").time() + 31 * 86400
        self.assertIsNotNone(P.maybe_purge(cfg, now=later))         # ครบเดือน → อีกครั้ง

    def test_purge_failure_retries_next_day(self):
        F.RPC = MockRPC(fail={"cash_purge_old"})
        self.assertIsNone(P.maybe_purge(cfg_for("BK")))
        self.assertFalse(os.path.exists(os.path.join(S.state_dir(), "cash_purge_last.txt")))
        m = MockRPC()
        F.RPC = m
        self.assertIsNone(P.maybe_purge(cfg_for("BK")))             # ไม่ยิงรัวทุกนาที
        later = __import__("time").time() + 86401
        self.assertIsNotNone(P.maybe_purge(cfg_for("BK"), now=later))

    def test_branch_lock_blocks(self):
        with F.branch_lock("BK", wait=1):
            res = {}
            def other():
                try:
                    with F.branch_lock("BK", wait=1):
                        res["got"] = True
                except TimeoutError:
                    res["got"] = False
            t = threading.Thread(target=other)
            t.start()
            t.join()
        self.assertFalse(res["got"])

    # ---------- poller ----------
    def test_poller_cut_and_export(self):
        root = tempfile.mkdtemp()
        try:
            m = MockRPC(tick={
                "pending_cuts": [{"round_id": 11, "branch": "BK", "round_date": "2026-10-02",
                                  "cut_pending_by": "button"}],
                "to_export": [{"round_id": 22, "branch": "SKN", "round_date": "2026-10-02",
                               "status": "late"}],
                "locked_late": [22]})
            F.RPC = m
            cfgs = {br: cfg_for(br) for br in P.BRANCHES}
            orig_today = F.today_th
            F.today_th = lambda: DAY
            try:
                summ = P.run_once(cfgs=cfgs, root=root)
            finally:
                F.today_th = orig_today
            self.assertEqual(m.names(), ["cash_tick", "cash_feed_docs", "cash_finalize_cut",
                                         "cash_export_out", "cash_mark_exported", "cash_purge_old"])
            self.assertEqual(summ["cut"], [11])
            self.assertEqual(summ["exported"], [22])
            path = os.path.join(root, "sales_report", "SKN", "261002_out.json")
            with open(path, encoding="utf-8") as f:
                out = json.load(f)
            self.assertEqual(set(out), OUT_KEYS)
            self.assertEqual(out["preparer"], "นัท")
            self.assertFalse(os.path.exists(path + ".tmp"))
        finally:
            shutil.rmtree(root)

    def test_poller_extra_pull(self):
        m = MockRPC(tick={"pending_cuts": [
            {"round_id": 31, "branch": "PPS", "round_date": "2026-10-02", "cut_pending_by": "button"},
            {"round_id": 30, "branch": "PPS", "round_date": "2026-10-01", "cut_pending_by": "button"}]})
        m.extra_ids = {31}
        F.RPC = m
        summ = P.run_once(cfgs={br: cfg_for(br) for br in P.BRANCHES}, root=tempfile.gettempdir())
        # สาขาเดียวดึง DBF ครั้งเดียว แม้มี 2 รอบค้าง
        self.assertEqual(m.names(), ["cash_tick", "cash_feed_docs", "cash_finalize_cut", "cash_finalize_cut",
                                     "cash_purge_old"])
        self.assertEqual(summ["cut"], [31, 30])
        self.assertEqual(summ["extra_pull"], [{"round_id": 31, "added": 2}])

    def test_poller_feed_fail_skips_finalize(self):
        m = MockRPC(tick={"pending_cuts": [{"round_id": 11, "branch": "BK",
                                            "round_date": "2026-10-02", "cut_pending_by": "button"}]},
                    fail={"cash_feed_docs"})
        F.RPC = m
        summ = P.run_once(cfgs={br: cfg_for(br) for br in P.BRANCHES}, root=tempfile.gettempdir())
        self.assertNotIn("cash_finalize_cut", m.names())
        self.assertEqual(summ["cut_failed"], [11])

    def test_poller_export_write_fail_no_mark(self):
        m = MockRPC(tick={"to_export": [{"round_id": 22, "branch": "SKN",
                                         "round_date": "2026-10-02", "status": "submitted"}]})
        F.RPC = m
        root = tempfile.mkdtemp()
        try:
            os.chmod(root, 0o500)            # เขียนไม่ได้
            summ = P.run_once(cfgs={br: cfg_for(br) for br in P.BRANCHES}, root=root)
        finally:
            os.chmod(root, 0o700)
            shutil.rmtree(root)
        self.assertNotIn("cash_mark_exported", m.names())
        self.assertEqual(summ["export_failed"], [22])


if __name__ == "__main__":
    unittest.main()
