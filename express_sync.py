# -*- coding: utf-8 -*-
"""
007 Metals - sync บิลขายจาก Express (ARTRN.DBF) -> express_bill (quote-app)

ทำไมต้องมี: express_bill เดิมมาจาก import ครั้งเดียว (243 ใบจาก window.IVDATA ของ
quote007) จึงมีแค่ 21-29 ก.ค. 2569 — บิลที่ออกหลังจากนั้นค้นไม่เจอ สคริปต์นี้ทำให้
ตารางตามทันของจริงทุกรอบ feeder โดยอ่านจาก ARTRN.DBF ซึ่งเป็นต้นทางเดียวกับที่
express_amount.py ใช้เติมยอด (ARTRN = ตัวจริงของระบบบัญชี)

กติกาที่ยึด (ตรวจกับข้อมูลจริงแล้ว 2026-08-14):
  · เอกสารขาย = RECTYP 1 (HS ขายเงินสด) / 3 (IV ใบกำกับ) เท่านั้น
    — ตรงนิยามเดียวกับ express_amount.py และ sopo_month.py
  · ข้าม DOCSTAT='C' (ยกเลิก, NETAMT=0 ทุกใบ) และถ้าใบที่เคย sync ไปแล้วถูกยกเลิก
    ทีหลัง จะลบออกจาก express_bill ให้ด้วย
  · key = (branch, DOCNUM) ไม่ใช่ DOCNUM เดี่ยว — Express เดินเลขแยกต่อสาขา
    เลขเดียวกันมีได้หลายสาขา (ต้องมี unique index branch,iv ตาม quote_amount_fix.sql)
  · cust = ARMAS.CUSNAM แปลง NBSP (\\xa0) เป็นช่องว่างปกติ — ตรงกับค่าที่ import เดิม
    เขียนไว้เป๊ะทั้ง 242 ใบที่จับคู่ได้ ⇒ รันแล้วไม่เกิด update ลอย ๆ
  · amount = NETAMT + amount_source='express' (เหตุผลว่าทำไมไม่คำนวณเอง ดู
    quote_amount_fix.sql §1) — สคริปต์นี้ทำงานของ express_amount.py ในตัวแล้ว
  · ไม่ส่งคอลัมน์ payload/amount_calc ไปด้วย → แถวเก่าที่ import มาจาก ivdata
    เก็บ payload ดิบไว้เหมือนเดิม (PostgREST update เฉพาะคอลัมน์ที่ส่งไป)

ค่าเริ่มต้นดึง "ทั้งหมดเท่าที่มีใน ARTRN" ไม่ใช้ window แบบ so_push เพราะ express_bill
คือคลังบิลไว้ค้นย้อนหลัง ถ้าตัด window แถวเก่าจะไม่มีวันถูกเติม (= บั๊กแบบเดียวกับที่
กำลังแก้อยู่นี้) รอบถัดไปไม่แพงเพราะเทียบ fingerprint กับ state file ก่อนยิง —
ไม่มีอะไรเปลี่ยนก็ไม่ยิงเลย

run_all.sh เรียกทุกรอบ (10 นาที) แต่สคริปต์คุมจังหวะตัวเองให้ทำงานจริงชั่วโมงละครั้ง
(EXPRESS_EVERY_MIN) — อ่าน ARTRN+ARMAS ครบทั้งไฟล์ทุกรอบผ่าน Drive proxy กินเวลา
~35 วิ/สาขา ซึ่งไม่คุ้มกับบิลที่ DBF เองก็เพิ่งขึ้น Drive ทุก ~15 นาที

usage: SUPABASE_SERVICE_KEY=<service_role_key> python3 express_sync.py <config_file>
                            [--dry] [--now] [--since YYYY-MM-DD]
   --now  ข้ามตัวจับเวลา EXPRESS_EVERY_MIN (ใช้ตอนรันมือ/backfill)

   ⚠️ ต้องใช้ service_role key (ส่งผ่าน env ไม่เขียนลงไฟล์ cfg) เพราะ quote_schema.sql
   ให้ express_bill อ่านได้แค่ authenticated และเขียนได้เฉพาะ service role —
   anon key ที่ feeder ใช้ปกติจะโดนปฏิเสธ ถ้าไม่มี key สคริปต์จะข้ามเงียบ ๆ
"""
import sys, os, json, time, hashlib, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S  # reuse PROXY / read_dbf / sb_request / load_config / state_dir / chunks / log

SALE_RECTYP = ("1", "3")   # HS ขายเงินสด / IV ใบกำกับ
CANCELLED = "C"            # DOCSTAT ของใบที่ยกเลิก
PUSH_BATCH = 200
DELETE_BATCH = 40
EVERY_MIN_DEFAULT = "60"   # ทำงานจริงทุกกี่นาที (run_all เรียกทุก 10 นาที) — 0 = ทุกรอบ


def norm(s):
    """ตัดช่องว่างหัวท้าย + แปลง NBSP ที่ติดมาจาก DBF ให้เป็น space ปกติ

    ค่าใน DBF มี \\xa0 ปนอยู่จริง (เช่น 'ปราณี\\xa0ด้วงหว้า') ซึ่งหน้าตาเหมือน space
    แต่ ILIKE ในหน้าค้นหาเทียบไบต์ตรง ๆ ไม่ถือว่าเท่ากัน — normalize ตั้งแต่ตอนเขียน
    """
    return (s or "").replace("\xa0", " ").strip()


def arg_since(argv, cfg):
    """--since YYYY-MM-DD > --since=YYYY-MM-DD > EXPRESS_SINCE ใน cfg > None (= ทั้งหมด)"""
    raw = None
    for i, a in enumerate(argv):
        if a == "--since" and i + 1 < len(argv):
            raw = argv[i + 1]
        elif a.startswith("--since="):
            raw = a.split("=", 1)[1]
    raw = raw or cfg.get("EXPRESS_SINCE") or ""
    return datetime.date.fromisoformat(raw.strip()) if raw.strip() else None


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = S.load_config(sys.argv[1])
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    dry = "--dry" in sys.argv
    if not dry and not (key and cfg.get("SUPABASE_URL")):
        S.log("EXPRESS_SYNC: ข้าม — ไม่มี SUPABASE_SERVICE_KEY (express_bill เขียนได้เฉพาะ service role)")
        return
    if key:
        cfg["SUPABASE_KEY"] = key

    branch = cfg["BRANCH"]
    src = cfg.get("SRC", "")
    if cfg.get("PROXY_URL"):
        S.PROXY.update(url=cfg["PROXY_URL"], token=cfg.get("PROXY_TOKEN", ""), branch=branch)
    since = arg_since(sys.argv, cfg)

    # ---- จับเวลา: run_all เรียกทุกรอบ แต่ทำงานจริงชั่วโมงละครั้งพอ ----
    # ประทับเวลาก่อนเริ่มงานหนัก ไม่ใช่ตอนจบ — รอบที่พังจะได้ไม่วนโหลด DBF ใหม่ทุก 10 นาที
    stamp = os.path.join(S.state_dir(), "express_last_%s.txt" % branch)
    every = int(os.environ.get("EXPRESS_EVERY_MIN") or cfg.get("EXPRESS_EVERY_MIN") or EVERY_MIN_DEFAULT)
    if every > 0 and not dry and "--now" not in sys.argv and os.path.exists(stamp):
        left = every * 60 - (time.time() - os.path.getmtime(stamp))
        if left > 0:
            S.log("EXPRESS_SYNC: %s ข้ามรอบนี้ (อีก %d นาทีถึงรอบถัดไป)" % (branch, left // 60 + 1))
            return
    if not dry:
        open(stamp, "w").write(datetime.datetime.now().isoformat())

    if not S._file_exists(os.path.join(src, "ARTRN.DBF")):
        S.log("EXPRESS_SYNC: %s ไม่มี ARTRN.DBF -> ข้าม" % branch)
        return

    names = {}
    for r in S.read_dbf(os.path.join(src, "ARMAS.DBF"), fields={"CUSCOD", "CUSNAM"}):
        names[r.get("CUSCOD", "")] = norm(r.get("CUSNAM"))

    rows, cancelled = {}, []
    for r in S.read_dbf(os.path.join(src, "ARTRN.DBF"),
                        fields={"RECTYP", "DOCNUM", "DOCDAT", "SONUM", "CUSCOD",
                                "NETAMT", "DOCSTAT"}):
        if (r.get("RECTYP") or "").strip() not in SALE_RECTYP:
            continue
        doc = (r.get("DOCNUM") or "").strip()
        if not doc:
            continue
        dat = r.get("DOCDAT")
        if since and (not dat or dat < since):
            continue
        if (r.get("DOCSTAT") or "").strip() == CANCELLED:
            cancelled.append(doc)
            continue
        cus = (r.get("CUSCOD") or "").strip()
        rows[doc] = {
            "branch": branch,
            "iv": doc,
            "so": (r.get("SONUM") or "").strip() or None,
            "cust": names.get(cus) or cus or None,
            "bill_date": S.d2s(dat),
            "amount": round(float(r.get("NETAMT") or 0), 2),
            "amount_source": "express",
        }

    # ---- เทียบกับรอบก่อน: ยิงเฉพาะใบใหม่/ใบที่ค่าเปลี่ยน ----
    state_file = os.path.join(S.state_dir(), "state_express_%s.json" % branch)
    try:
        state = json.load(open(state_file, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}

    changed = []
    for doc, row in rows.items():
        fp = hashlib.md5(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if state.get(doc) != fp:
            changed.append((doc, fp))

    cfp = hashlib.md5(json.dumps(sorted(cancelled)).encode()).hexdigest()
    drop = cancelled if state.get("__cancelled__") != cfp else []

    S.log("EXPRESS_SYNC: %s บิลขาย %d ใบ · เปลี่ยน %d · ยกเลิกต้องลบ %d%s%s" %
          (branch, len(rows), len(changed), len(drop),
           " · ตั้งแต่ %s" % since if since else "", " (DRY)" if dry else ""))
    for doc, _ in changed[:5]:
        r = rows[doc]
        S.log("   %s %s %s %.2f" % (r["bill_date"], doc, (r["cust"] or "")[:28], r["amount"]))
    if dry or (not changed and not drop):
        return

    for batch in S.chunks([rows[doc] for doc, _ in changed], PUSH_BATCH):
        S.sb_request(cfg, "POST", "/rest/v1/express_bill?on_conflict=branch,iv",
                     batch, prefer="resolution=merge-duplicates,return=minimal")

    # ใบที่ถูกยกเลิกใน Express: เอาออกจากตารางค้น ไม่งั้นค้างเป็นบิลผีที่ยอด 0
    for batch in S.chunks(drop, DELETE_BATCH):
        S.sb_request(cfg, "DELETE", "/rest/v1/express_bill?branch=eq.%s&iv=in.(%s)"
                     % (branch, ",".join('"%s"' % d for d in batch)))

    for doc, fp in changed:
        state[doc] = fp
    state["__cancelled__"] = cfp
    if since is None:
        # อ่านครบทุกใบอยู่แล้ว -> ตัด entry ที่ไม่มีใน ARTRN ทิ้งได้ (ใบที่โดนยกเลิก/ลบ)
        state = {k: v for k, v in state.items() if k in rows or k == "__cancelled__"}
    json.dump(state, open(state_file, "w", encoding="utf-8"))
    S.log("EXPRESS_SYNC: %s push %d ใบ · ลบ %d ใบ OK" % (branch, len(changed), len(drop)))


if __name__ == "__main__":
    main()
