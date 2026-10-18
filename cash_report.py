# -*- coding: utf-8 -*-
"""
007 Metals - สรุปยอดขายประจำวัน (C113–C122 ของชีตรายงานขาย Excel) ให้แอปปิดรอบ 007 แสดงแทน Excel
CTO 18 ต.ค. 69: "ปิดรอบต้องโชว์ยอดขายจริง และเงินที่ได้รับ เงินสด/โอน แยกกัน + ส่วนที่เข้าบัญชี
ทุกอย่างต้องตรงตามรายงานขาย — Excel จะเลิกใช้" · แผน ~/CTOdrive/reports/cash_สรุป19แถว_แผน_261018.md

คำนวณด้วยตัวเดียวกับที่เติมชีต "<วัน>_auto" (cash_excel_fill.build_plan: Express IV/AI/SR/RE/ARRCPIT
+ ช่องทางที่พนักงานเลือกในแอป) แล้วรวมเป็นแถวสรุปตามสูตรในชีตจริง · ตัวเลขจึงตรงกับ Excel ที่แอปเติม
  C113 ยอดรับชำระหนี้ตามโปรแกรม Express = F63+H63+I63+H110−F82−H82−I82
  C114/C115/C116 = H63/I63/F63 (IV วันนี้: โอน Kbank / QR / เงินสด)
  C117/C118/C119 = F73/H73/I73 (มัดจำ AI: เงินสด / โอน / QR)
  C120 = J110 (วางบิลชำระด้วยเงินสด) · C121 = F82 (คืนสินค้าจ่ายคืนเงินสด) · C122 = K130 (ค่าใช้จ่าย)
แถว C123–C131 (ยกมา/คงเหลือตามระบบ/นับได้/ส่วนต่าง/เข้าบัญชี/ยกไป/ผู้จัดทำ) คิดใน Supabase
(cash_sales_report · sql/015) เพราะขึ้นกับยอดนับ/ยอดยกไปที่พนักงานกรอก

push: RPC cash_feed_report(p_branch, p_date, p_report) ด้วย service key · cash_poller เรียก push_due()
ทุกรอบ (1 นาที) แต่คำนวณใหม่เฉพาะเมื่อ fingerprint เปลี่ยน (ARTRN/ARRCPIT mtime + ข้อมูลรอบในแอป)

usage: python3 cash_report.py BR YYYY-MM-DD [--push]   (ค่าเริ่ม = พิมพ์ผล ไม่ push)
ไม่ลงชื่อลูกค้าใน log/report (PDPA) — report มีแต่ยอดรวม/จำนวนใบ
"""
import sys, os, json, time, hashlib, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S
import cash_feed as F
import cash_excel_fill as XL

VERSION = 4

# ฟอร์ม Excel มีแถวจำกัด (IV 60 · AI 8 · SR 7 · วางบิล 20 · ค่าใช้จ่าย 16) — build_plan ตัดท้ายเมื่อเกิน
# สรุปรายวันต้องรวมครบทุกใบ (SKN 14/10: SR 11 ใบ วางบิล 40 รายการ) จึงคำนวณด้วยช่วงแถวเสมือนที่ไม่ชนกัน
_REPORT_ROWS = {"IV_ROWS": range(1000, 3000), "AI_ROWS": range(3000, 4000), "SR_ROWS": range(4000, 5000),
                "PAID_ROWS": range(5000, 8000), "EXP_ROWS": range(8000, 9000)}


class _unbounded(object):
    """ช่วงแถวของ cash_excel_fill ชั่วคราว (เฉพาะคำนวณสรุป ไม่ใช่เติม Excel)"""
    def __enter__(self):
        self.old = {k: getattr(XL, k) for k in _REPORT_ROWS}
        for k, v in _REPORT_ROWS.items():
            setattr(XL, k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(XL, k, v)



def _sum(plan, rows, col):
    return round(sum(float(plan.cells.get("%s%d" % (col, r)) or 0) for r in rows
                     if isinstance(plan.cells.get("%s%d" % (col, r)), (int, float))), 2)


def app_cash(detail):
    """เงินสดของใบในรอบ (RE/AI/IV ที่ลงว่าจ่ายแล้ว) ตามช่องทางที่พนักงานเลือก — ฐานเดียวกับ cash__calc"""
    tot = 0.0
    for d in (detail or {}).get("docs") or []:
        if d.get("doc_type") not in ("RE", "AI", "IV"):
            continue
        m = XL._money_of(d, XL._num(d.get("amount_now")))
        if m:
            tot += m[0]
    return round(tot, 2)


def summarize(plan, ex=None):
    """Plan (cash_excel_fill.build_plan) → dict สรุป C113–C122 + จำนวนใบ (ไม่มีชื่อลูกค้า)"""
    iv = {c: _sum(plan, XL.IV_ROWS, c) for c in "EFGHI"}
    ai = {c: _sum(plan, XL.AI_ROWS, c) for c in "FGHI"}
    sr = {c: _sum(plan, XL.SR_ROWS, c) for c in "FGHI"}
    paid_bill = _sum(plan, XL.PAID_ROWS, "H")     # H110 ยอดรวมบิลที่วางบิลชำระ
    paid_cash = _sum(plan, XL.PAID_ROWS, "J")     # J110 ส่วนเงินสด
    paid_xfer = _sum(plan, XL.PAID_ROWS, "D")     # D = ยอดโอนเข้าบัญชีของแถววางบิล
    expense = _sum(plan, XL.EXP_ROWS, "K")        # K130
    r = lambda v: round(v, 2)
    out = {
        "version": VERSION,
        # − ส่วนที่แถววางบิลลงเต็มยอดแต่หักคืนสินค้าในใบเสร็จโอน/QR (ลดหนี้ ไม่ได้รับเงิน) — รีวิว 18 ต.ค. 69
        "c113": r(iv["F"] + iv["H"] + iv["I"] + paid_bill - sr["F"] - sr["H"] - sr["I"]
                  - float(getattr(plan, "paid_unfilled_short", 0) or 0)),
        "c114": iv["H"], "c115": iv["I"], "c116": iv["F"],
        "c117": ai["F"], "c118": ai["H"], "c119": ai["I"],
        "c120": paid_cash, "c121": sr["F"], "c122": expense,
        # ประกอบ (ไม่อยู่ใน 19 แถว แต่ช่วยอ่าน/ตรวจ)
        "iv_credit": iv["G"], "iv_deposit_used": iv["E"],
        "paid_bill_total": paid_bill, "paid_transfer": paid_xfer,
        "sr_credit": sr["G"], "sr_transfer": sr["H"], "sr_qr": sr["I"],
        "counts": dict(plan.stats),
        "notes": list(plan.notes)[:20],
    }
    out["sr_cash_refund"] = sr["F"]
    if ex is not None:
        out["sr_count"] = len(ex.get("srs") or [])
        out["sr_comm_count"] = len(ex.get("sr_comm") or ())
    return out


def compute(branch, day, cfg, detail=None):
    """อ่าน Express + รอบในแอป → (report, fingerprint)"""
    src = cfg.get("SRC", "")
    if cfg.get("PROXY_URL"):
        S.PROXY.update(url=cfg["PROXY_URL"], token=cfg.get("PROXY_TOKEN", ""), branch=branch)
    else:
        S.PROXY.clear()
    basis_at = datetime.datetime.now(F.TH).isoformat(timespec="seconds")
    if detail is None:
        detail = XL.fetch_round(cfg, branch, day) or {}
    extra = [d["doc_no"] for d in (detail.get("docs") or []) if d.get("doc_type") == "RE"]
    F.clear_cache()
    ex = XL.load_express(src, day, extra)
    _round_scope(ex, detail, src)
    with _unbounded():
        plan = XL.build_plan(ex, detail, day)
        rep = summarize(plan, ex)
    # cash__calc (sql/015): คาดในลิ้นชัก = ยกมา + เงินสดตามแอป + cash_adjust − ค่าใช้จ่าย
    #   cash_adjust = (C116+C117+C120−C121) − เงินสด RE/AI ตามแอป → ส่วนที่ Excel นับต่างจากยอดใบเสร็จ
    #   (SR จ่ายคืนสด · ใบเสร็จหัก SR/AI ในตัวที่ลงเป็นเงินสดเต็มยอด) โดยยังใช้ช่องทางสดของแอปตอนส่ง
    rep["app_cash"] = app_cash(detail)
    rep["cash_adjust"] = round(rep["c116"] + rep["c117"] + rep["c120"] - rep["c121"] - rep["app_cash"], 2)
    rep["computed_from"] = {"round_id": (detail.get("round") or {}).get("id"),
                            "has_round": bool(detail.get("round"))}
    # เวลาที่อ่านข้อมูลรอบ (ก่อนคำนวณ) — cash_submit ใช้ตัดสินว่าสรุปยังทันการเลือกช่องทางล่าสุดไหม (sql/015)
    rep["basis_at"] = basis_at
    return rep


def _round_scope(ex, detail, src):
    """ผูกใบเสร็จ/SR กับ "รอบ" ของแอป (ตัด 20:00) แทน "วันที่เอกสาร" — รีวิว 18 ต.ค. 69 ข้อ 3
    รอบตัดแล้ว: ใบเสร็จหลังตัดรอบ (ไม่อยู่ในรอบ) ไม่นับวันนี้ · SR ที่ใบเสร็จนั้นหักไว้ย้ายไปรอบถัดไปด้วย
    SR ที่ใบเสร็จยกมาหักไว้ (ลงวันก่อน) มานับในรอบนี้ — เงินกับ SR อยู่รอบเดียวกันเสมอ"""
    rnd = (detail or {}).get("round") or {}
    if not rnd or not rnd.get("cut_at"):
        return
    in_round = {d.get("doc_no") for d in detail.get("docs") or [] if d.get("doc_type") == "RE"}
    # ใบเสร็จยอด ≤ 0 ของวัน (จ่ายค่า%ช่าง/คืนเงินสด เช่น SKN 13/10 RE0027211 −15,344) ไม่เข้ารอบในแอปอยู่แล้ว
    #   (sql/009 รับเฉพาะยอด > 0) — ต้องคงไว้ ไม่งั้น SR ที่หักในใบนั้นถูกตัดทิ้ง ยอดที่ควรมีสูงเกิน (รีวิวรอบ 2)
    in_round |= {k for k, v in ex["res"].items() if XL._num(v.get("NETAMT")) <= 0}
    # ใบเสร็จที่ชำระ IV ซึ่งพนักงานลงว่าจ่ายแล้วในรอบนี้ (doc_type IV) ก็อยู่ในรอบ — เงินนับจากใบ IV
    #   แต่ SR ที่ใบเสร็จนั้นหักไว้ (เช่น %ช่าง BK 17/10 SR0005167 ใน RE0061962) ต้องยังนับ (ตรวจกับ production 18 ต.ค.)
    iv_docs = {d.get("doc_no") for d in detail.get("docs") or [] if d.get("doc_type") == "IV"}
    in_round |= {rn for rn, ls in (ex.get("lines") or {}).items()
                 if any(ln["rectyp"] == "3" and ln["doc_no"] in iv_docs for ln in ls)}
    netted = {}
    for rn, ls in (ex.get("lines") or {}).items():
        for ln in ls:
            if ln["rectyp"] == "5":
                netted.setdefault(ln["doc_no"], rn)
    ex["res"] = {k: v for k, v in ex["res"].items() if k in in_round}
    keep = [s for s in ex["srs"]
            if netted.get((s.get("DOCNUM") or "").strip()) is None
            or netted.get((s.get("DOCNUM") or "").strip()) in in_round]
    # SR ที่ใบเสร็จ "ยกมา" (ออกหลังตัดรอบวันก่อน) หักไว้ และลงวันเดียวกับใบเสร็จนั้น → มานับรอบนี้ (ตามเงิน)
    #   SR ลงวันอื่นที่หักในใบเสร็จของวันนี้ ยังนับตามวันที่ SR (ตรง Excel SKN 16/10 SR0003272/73)
    carried = {d.get("doc_no") for d in detail.get("docs") or [] if d.get("doc_type") == "RE" and d.get("carried")}
    have = {(s.get("DOCNUM") or "").strip() for s in keep}
    need = {sr: rn for sr, rn in netted.items() if rn in carried and sr not in have}
    if need:
        for r in S.read_dbf(XL._find(src, "ARTRN.DBF"), fields={"RECTYP", "DOCNUM", "DOCDAT", "CUSCOD", "NETAMT",
                                                                 "DOCSTAT", "YOUREF", "SONUM", "AREACOD"}):
            doc = (r.get("DOCNUM") or "").strip()
            if doc in need and (r.get("RECTYP") or "").strip() == "5" and (r.get("DOCSTAT") or "").strip() != F.CANCELLED \
                    and r.get("DOCDAT") == (ex["res"].get(need[doc]) or {}).get("DOCDAT"):
                keep.append(r)
    ex["srs"] = sorted(keep, key=lambda r: (r.get("DOCNUM") or ""))


def _src_stamp(cfg):
    """mtime+size ของ ARTRN/ARRCPIT (ไฟล์ในเครื่อง) · ผ่าน proxy = ใช้ช่วงเวลา 5 นาทีแทน"""
    src = cfg.get("SRC", "")
    if cfg.get("PROXY_URL") or not src:
        return "proxy-%d" % (int(time.time()) // 300)
    parts = []
    for n in ("ARTRN.DBF", "ARRCPIT.DBF", "STCRD.DBF"):
        p = XL._find(src, n)
        try:
            st = os.stat(p)
            parts.append("%s:%d:%d" % (n, int(st.st_mtime), st.st_size))
        except OSError:
            parts.append("%s:-" % n)
    return "|".join(parts)


def _detail_stamp(detail):
    """เฉพาะส่วนที่กระทบสรุป: ช่องทางของใบ · ค่าใช้จ่าย · สถานะรอบ"""
    rnd = detail.get("round") or {}
    # chosen_at/cut_at ด้วย: เลือกช่องทางเดิมซ้ำ หรือดึงบิลเพิ่มโดยไม่มีใบใหม่ ก็ต้อง push ใหม่ ให้ basis_at
    #   ใหม่กว่าการเปลี่ยนล่าสุด ไม่งั้น cash_submit ตอบ CR001 ค้าง (รีวิวรอบ 2)
    docs = sorted((d.get("doc_no"), d.get("channel"), d.get("cash_amount"), d.get("noncash_channel"),
                   d.get("amount_now"), d.get("carried"), d.get("chosen_at")) for d in detail.get("docs") or [])
    exps = sorted((e.get("id"), e.get("amount"), e.get("deleted_at")) for e in detail.get("expenses") or [])
    blob = json.dumps([rnd.get("id"), rnd.get("status"), rnd.get("cut_at"), docs, exps], default=str, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _state_path():
    return os.path.join(S.state_dir(), "cash_report_state.json")


def _load_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st):
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, _state_path())


def unlocked_days(cfg):
    """รอบที่ CTO ปลดล็อก (พนักงานกำลังแก้/ส่งใหม่) → คำนวณสรุปใหม่ด้วยแม้เก่ากว่าเมื่อวาน (รีวิวข้อ 2)"""
    try:
        rows = XL._rest_get(cfg, "/rest/v1/cash_round?status=eq.unlocked&select=branch,round_date") or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["branch"], set()).add(datetime.date.fromisoformat(r["round_date"]))
    return out


def push(cfg, branch, day, rep):
    F.RPC(cfg, "cash_feed_report", {"p_branch": branch, "p_date": day.isoformat(), "p_report": rep})


def push_due(cfg_of, branches, days, force=False, db_now=None):
    """cash_poller: คำนวณ/ push เฉพาะสาขา-วันที่ fingerprint เปลี่ยน · คืน [(br, date, 'pushed'|'same'|'error')]"""
    st = _load_state()
    out = []
    extra = {}
    try:
        extra = unlocked_days(cfg_of(branches[0]))
    except Exception:
        pass
    for br in branches:
        for day in sorted(set(days) | extra.get(br, set())):
            key = "%s|%s" % (br, day.isoformat())
            try:
                cfg = cfg_of(br)
                # เวลา DB (cash_tick.now ที่ poller เรียกก่อน) — กันนาฬิกา Mac mini ไม่ตรง DB (รีวิวรอบ 2)
                basis_at = db_now or datetime.datetime.now(F.TH).isoformat(timespec="seconds")
                detail = XL.fetch_round(cfg, br, day) or {}
                fp = "%d|%s|%s" % (VERSION, _src_stamp(cfg), _detail_stamp(detail))
                if not force and st.get(key) == fp:
                    out.append((br, day.isoformat(), "same"))
                    continue
                rep = compute(br, day, cfg, detail)
                rep["basis_at"] = basis_at
                push(cfg, br, day, rep)
                st[key] = fp
                st.pop("err|" + key, None)
                out.append((br, day.isoformat(), "pushed"))
            except F.RpcMissing:
                raise
            except Exception as e:
                # log ครั้งเดียวต่อปัญหา (กัน alert_scan แจ้ง LINE ซ้ำทุกนาที — รีวิวข้อ 8) · ยังลองใหม่ทุกรอบ
                msg = "%s: %s" % (type(e).__name__, e)
                if st.get("err|" + key) != msg:
                    S.log("ERROR: CASH_REPORT %s %s %s" % (br, day, msg))
                    st["err|" + key] = msg
                out.append((br, day.isoformat(), "error"))
    # เก็บ state ไม่เกิน 10 วันล่าสุด
    lim = (max(days) - datetime.timedelta(days=40)).isoformat() if days else ""
    keep = {k: v for k, v in st.items() if k.split("|")[-1] >= lim}
    _save_state(keep)
    return out


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    br, day = argv[1].upper(), datetime.date.fromisoformat(argv[2])
    cfg = S.load_config(os.path.join(S.state_dir(), "cfg_%s.txt" % br))
    rep = compute(br, day, cfg)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if "--push" in argv:
        push(cfg, br, day, rep)
        print("pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
