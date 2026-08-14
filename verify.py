# -*- coding: utf-8 -*-
"""
007 Metals - ตัวตรวจว่าตัวเลขใน Supabase ตรงกับ DBF จริงไหม

ทำไมต้องมี: บั๊กตัวเลขทั้งชุดที่เจอวันที่ 14 ส.ค. 69 (ยอดสะสมขาด 2 เดือน · มัดจำหาย
20.2M · เอาขายระหว่างสาขาไปปนยอดขาย 33.3M · GP คิดคนละชุดบิลกับยอดขาย) ไม่มีอันไหน
ถูกจับได้เอง — เจอเพราะบังเอิญเปิด SOPO เดิมมาทาบทีละบรรทัด ถ้าไม่มีตัวตรวจ รอบหน้า
ก็ตกหล่นอีกแบบเดิม ไฟล์นี้คือตาข่าย: คำนวณใหม่จาก DBF แล้วทาบกับที่อยู่ใน Supabase
ทุกรอบ ผิดเมื่อไหร่ขึ้น log ทันทีภายใน 10 นาที ไม่ใช่รู้อีกทีตอนสิ้นไตรมาส

⚠️ กติกาในไฟล์นี้ "เขียนซ้ำโดยตั้งใจ" ไม่ import มาจาก sopo_month.py — ถ้า import
   ตัวตรวจจะเห็นด้วยกับความเข้าใจผิดของสคริปต์หลักเสมอ จับได้แค่ท่อส่งพัง จับ
   ตรรกะผิดไม่ได้ ที่มาของกติกาอ้างเลขบรรทัดใน build_all.py ของ SOPO เดิมกำกับไว้

usage: python3 verify.py <config_file> [--now]
   ปกติ run_all.sh เรียกให้ทุกรอบ แต่ทำงานจริงชั่วโมงละครั้ง (VERIFY_EVERY_MIN)
   ใส่ SUPABASE_SERVICE_KEY ทาง env ด้วยจะตรวจ express_bill ให้ (ไม่ใส่ก็ข้ามส่วนนั้น)

อ่าน log: grep VERIFY ~/007so_push/feeder.log   ·   ดูเฉพาะที่ผิด: grep 'VERIFY-FAIL'
"""
import sys, os, re, json, datetime, urllib.request, urllib.error, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S

EVERY_MIN_DEFAULT = "60"
MONEY_TOL = 1.0      # บาท — กันเศษปัดจากการ round ทีละแถว
SALE_RECTYP = ("1", "3")

# ---- กติกาแยกประเภทลูกค้า: build_all.py:68-71 (_IBNUM/_isib) และ :220-221 ----
_IBNUM = ("042090090", "042471", "042491", "042492", "061031")
PLATFORMS = ("SHOPEE", "TIKTOK", "LAZADA", "SHOPEEPAY", "NOCNOC")

# ---- หมุดที่รู้คำตอบอยู่แล้ว: หมุดไมล์ "138M แซงทั้งปี 2025" ในจอ Bonus Race เดิม ----
# ถ้าเลขนี้ขยับแปลว่านิยาม "ยอดขาย" เพี้ยนไปจากที่ทั้งบริษัทใช้กันมา ต้องรู้ทันที
ANCHOR_2025_STORE = 138_559_956.0


def segment(cuscod, name):
    c = (cuscod or "").strip()
    if re.sub(r"^[^0-9]+", "", c).startswith(_IBNUM):
        return "interbranch"
    if c[:1].upper() == "O":
        return "online"
    if any(p in (name or "").upper() for p in PLATFORMS):
        return "online"
    return "regular"


def sb_get(cfg, path, key=None):
    url = cfg["SUPABASE_URL"].rstrip("/") + path
    k = key or cfg["SUPABASE_KEY"]
    req = urllib.request.Request(url, headers={"apikey": k, "Authorization": "Bearer " + k})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def sb_get_all(cfg, path, key=None, page=1000):
    """อ่านทุกแถวด้วย Range pagination — PostgREST ตัดที่ 1000 แถว/คำขอ (max-rows)
    ถ้าใช้ sb_get เฉย ๆ กับตารางใหญ่ ตัวตรวจจะรวมยอดจากแถวไม่ครบแล้วฟ้องผิด ๆ เอง"""
    k = key or cfg["SUPABASE_KEY"]
    out, offset = [], 0
    while True:
        req = urllib.request.Request(cfg["SUPABASE_URL"].rstrip("/") + path, headers={
            "apikey": k, "Authorization": "Bearer " + k,
            "Range-Unit": "items", "Range": "%d-%d" % (offset, offset + page - 1),
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            chunk = json.loads(resp.read())
        out.extend(chunk)
        if len(chunk) < page:
            return out
        offset += page


def sb_count(cfg, path, key=None):
    """นับแถวจริงผ่าน Content-Range — GET ธรรมดา PostgREST คืนสูงสุด 1000 แถว นับเองไม่ได้"""
    url = cfg["SUPABASE_URL"].rstrip("/") + path
    k = key or cfg["SUPABASE_KEY"]
    req = urllib.request.Request(url, headers={
        "apikey": k, "Authorization": "Bearer " + k,
        "Prefer": "count=exact", "Range": "0-0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return int(resp.headers.get("Content-Range", "0-0/0").split("/")[-1])


class Report:
    """เก็บผลตรวจ แล้วสรุปทีเดียวตอนจบ — log ยาวไม่มีใครอ่าน สรุปสั้นถึงจะได้ผล"""

    def __init__(self, branch):
        self.branch = branch
        self.fails = []
        self.n = 0

    def check(self, name, got, want, tol=0.0):
        self.n += 1
        ok = abs(float(got) - float(want)) <= tol
        if not ok:
            self.fails.append((name, got, want))
        return ok

    def check_live(self, name, got, want, tol=0.0):
        """เดือนที่ยังไม่ปิด: DBF เดินหน้าตลอด DB ตามหลังได้ตามปกติ (feeder รันทุก 10 นาที)
        ฟ้องเฉพาะกรณีที่ผิดจริง — DB มากกว่า DBF (เป็นไปไม่ได้) หรือตามหลังเกิน 10% (feeder ค้าง)
        ถ้าฟ้องทุกรอบเพราะบิลเพิ่งออก คนจะเลิกอ่าน log แล้วของจริงจะหลุดไปด้วย"""
        self.n += 1
        got, want = float(got), float(want)
        if got > want + tol:
            self.fails.append((name + " (DB มากกว่าที่มีจริง)", got, want))
        elif want > 0 and (want - got) / want > 0.10:
            self.fails.append((name + " (ตามหลังเกิน 10% — feeder ค้างหรือเปล่า)", got, want))

    def note_fail(self, name, detail):
        self.n += 1
        self.fails.append((name, detail, None))

    def done(self):
        if not self.fails:
            S.log("VERIFY: %s ผ่านครบ %d ข้อ" % (self.branch, self.n))
            return
        S.log("VERIFY-FAIL: %s ไม่ผ่าน %d จาก %d ข้อ" % (self.branch, len(self.fails), self.n))
        for name, got, want in self.fails[:15]:
            if want is None:
                S.log("   ✗ %s: %s" % (name, got))
            else:
                S.log("   ✗ %s: ใน DB %s · จาก DBF %s · ต่าง %s"
                      % (name, _f(got), _f(want), _f(float(got) - float(want))))


def _f(x):
    return "{:,.2f}".format(x) if isinstance(x, float) else "{:,}".format(x)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = S.load_config(sys.argv[1])
    if not cfg.get("SUPABASE_URL") or not cfg.get("SUPABASE_KEY"):
        return
    branch = cfg["BRANCH"]
    src = cfg.get("SRC", "")
    if cfg.get("PROXY_URL"):
        S.PROXY.update(url=cfg["PROXY_URL"], token=cfg.get("PROXY_TOKEN", ""), branch=branch)
    svc = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    stamp = os.path.join(S.state_dir(), "verify_last_%s.txt" % branch)
    every = int(os.environ.get("VERIFY_EVERY_MIN") or cfg.get("VERIFY_EVERY_MIN") or EVERY_MIN_DEFAULT)
    if every > 0 and "--now" not in sys.argv and os.path.exists(stamp):
        if os.path.getmtime(stamp) > (datetime.datetime.now().timestamp() - every * 60):
            return
    open(stamp, "w").write(datetime.datetime.now().isoformat())

    rep = Report(branch)
    today = datetime.date.today()

    # ---------- คำนวณใหม่จาก DBF ----------
    names = {}
    for r in S.read_dbf(os.path.join(src, "ARMAS.DBF"), fields={"CUSCOD", "PRENAM", "CUSNAM"}):
        names[r.get("CUSCOD", "")] = (r.get("PRENAM", "") + " " + r.get("CUSNAM", "")).strip()

    exp = collections.defaultdict(lambda: collections.Counter())  # month -> ยอดแต่ละประเภท
    exp_day = collections.defaultdict(lambda: collections.Counter())  # month -> {วัน: ยอด}
    bills = 0  # บิลขายที่ไม่ถูกยกเลิก (เทียบกับ express_bill)
    sr_mk = {}  # เอกสาร SR -> month (ไว้รวมบรรทัดใน STCRD เทียบ ret+comm+plat)
    for r in S.read_dbf(os.path.join(src, "ARTRN.DBF"),
                        fields={"RECTYP", "DOCNUM", "DOCDAT", "CUSCOD", "NETAMT", "ADVAMT",
                                "DISCAMT", "SLMCOD", "DOCSTAT"}):
        dat = r.get("DOCDAT")
        if not dat:
            continue
        mk = "%04d-%02d" % (dat.year, dat.month)
        rectyp = (r.get("RECTYP") or "").strip()
        if (r.get("DOCSTAT") or "").strip() == "C":
            continue
        net = float(r.get("NETAMT") or 0)
        if rectyp == "0":
            exp[mk]["ai_tot"] += net
            exp[mk]["ai_cnt"] += 1
        elif rectyp == "5":
            sr_mk[(r.get("DOCNUM") or "").strip()] = mk
        elif rectyp in SALE_RECTYP:
            bills += 1
            cc = (r.get("CUSCOD") or "").strip()
            v = net + float(r.get("ADVAMT") or 0)  # ยอดเต็ม build_all.py:217
            seg = segment(cc, names.get(cc, ""))
            exp[mk][seg] += v
            if seg != "interbranch":
                exp_day[mk][str(dat.day)] += v
            if seg == "regular":
                exp[mk]["bill_count"] += 1
                # บิลหน้าร้านที่ไม่มีรหัสเซลล์ -> เข้ายอดสาขาแต่ไม่เข้ายอดรายคน (มีจริงเดือนละ 0-2 ใบ)
                if not (r.get("SLMCOD") or "").strip():
                    exp[mk]["no_slm"] += v
                # ส่วนลดท้ายบิลลด NETAMT แต่ไม่ลด TRNVAL รายบรรทัด -> gp_base สูงกว่ายอดขายได้เท่านี้
                exp[mk]["discamt"] += float(r.get("DISCAMT") or 0)

    # SR แยกก้อน + ยอดรับซื้อ: นับจากบรรทัดใน STCRD (แหล่งเดียวกับ feeder แต่เขียนตรวจแยก)
    for r in S.read_dbf(os.path.join(src, "STCRD.DBF"), fields={"DOCNUM", "DOCDAT", "TRNVAL"}):
        doc = (r.get("DOCNUM") or "").strip()
        if doc.startswith("RR"):
            dat = r.get("DOCDAT")
            if dat:
                exp["%04d-%02d" % (dat.year, dat.month)]["buy_tot"] += float(r.get("TRNVAL") or 0)
        elif doc in sr_mk:
            amt = float(r.get("TRNVAL") or 0)
            if amt > 0:
                exp[sr_mk[doc]]["sr_lines"] += amt

    # ---------- ทาบกับ Supabase ----------
    rows = sb_get(cfg, "/rest/v1/sopo_branch_month?select=*&branch=eq.%s" % branch)
    db = {r["month"]: r for r in rows}

    # 1) ยอดแต่ละเดือนต้องตรงกับที่คำนวณจาก DBF
    live_mk = "%04d-%02d" % (today.year, today.month)
    for mk, r in sorted(db.items()):
        e = exp.get(mk)
        if e is None:
            rep.note_fail("%s มีใน DB แต่ไม่มีบิลใน DBF" % mk, "ยอด %s" % _f(float(r["sales_tot"] or 0)))
            continue
        chk = rep.check_live if mk == live_mk else rep.check
        chk("%s sales_tot" % mk, float(r["sales_tot"] or 0), e["regular"], MONEY_TOL)
        chk("%s online_tot" % mk, float(r["online_tot"] or 0), e["online"], MONEY_TOL)
        chk("%s interbranch_tot" % mk, float(r["interbranch_tot"] or 0), e["interbranch"], MONEY_TOL)
        chk("%s ai_tot" % mk, float(r["ai_tot"] or 0), e["ai_tot"], MONEY_TOL)
        chk("%s bill_count" % mk, float(r.get("bill_count") or 0), e["bill_count"], 0)
        chk("%s buy_tot" % mk, float(r.get("buy_tot") or 0), e["buy_tot"], MONEY_TOL)
        # ret+comm+plat = ผลรวมบรรทัด SR ทั้งหมด (การแยกก้อนตรวจไม่ได้โดยไม่ก๊อปกติกามา
        # แต่ผลรวมต้องไม่ตกหล่น — บรรทัดที่หายไปจะโผล่ที่นี่)
        chk("%s ret+comm+plat = บรรทัด SR" % mk,
            float(r["ret_tot"] or 0) + float(r.get("comm_tot") or 0) + float(r.get("plat_tot") or 0),
            e["sr_lines"], MONEY_TOL)

        # 2) ยอดรายวันรวมกันต้องเท่ายอดเดือน (หน้าร้าน+ออนไลน์)
        dt = r.get("day_tot") or {}
        if dt:
            chk("%s day_tot รวม" % mk, sum(float(x) for x in dt.values()),
                e["regular"] + e["online"], MONEY_TOL)

    # 3) เดือนต้องครบตั้งแต่ ม.ค. ปีนี้ถึงเดือนนี้ — บั๊ก MONTHS_BACK=6 เคยทำให้ขาด ม.ค.-ก.พ.
    missing = [mk for mk in ("%d-%02d" % (today.year, m) for m in range(1, today.month + 1))
               if mk not in db and exp.get(mk)]
    if missing:
        rep.note_fail("เดือนหายจาก sopo_branch_month", ", ".join(missing))
    else:
        rep.n += 1

    # 4) ยอดรายคนรวมกันต้องเท่ายอดหน้าร้านของเดือนนั้น + GP ต้องคิดบนชุดบิลเดียวกัน
    prows = sb_get(cfg, "/rest/v1/sopo_person_month?select=*&branch=eq.%s" % branch)
    pm = collections.defaultdict(lambda: collections.Counter())
    for r in prows:
        pm[r["month"]]["net_sales"] += float(r["net_sales"] or 0)
        pm[r["month"]]["gp_base"] += float(r["gp_base"] or 0)
    for mk, p in sorted(pm.items()):
        e = exp.get(mk, collections.Counter())
        if mk in db:
            # ยอดรายคนรวม + บิลที่ไม่มีรหัสเซลล์ ต้องเท่ายอดหน้าร้านพอดี
            # เดือนที่ยังไม่ปิดผ่อนให้ 1% เพราะ no_slm มาจาก DBF (สด) แต่อีกสองตัวมาจาก DB (ตามหลัง)
            st = float(db[mk]["sales_tot"] or 0)
            rep.check("%s ผลรวมรายคน (+บิลไม่มีรหัสเซลล์) = sales_tot" % mk,
                      p["net_sales"] + e["no_slm"], st,
                      max(MONEY_TOL, st * 0.01) if mk == live_mk else MONEY_TOL)
        # gp_base = มูลค่าบรรทัดที่มีต้นทุนจริง สูงกว่ายอดขายได้ไม่เกินส่วนลดท้ายบิล
        # ถ้าเกินกว่านั้น = กำลังคิดกำไรจากบิลคนละชุดกับที่นับเป็นยอดขาย (บั๊กเดิม 14 ส.ค.)
        ceiling = p["net_sales"] + e["discamt"] + MONEY_TOL
        if p["gp_base"] > ceiling:
            rep.note_fail("%s gp_base เกินเพดาน" % mk,
                          "%s > %s (ยอดขาย+ส่วนลดท้ายบิล) — GP คิดคนละชุดบิลกับยอดขาย"
                          % (_f(p["gp_base"]), _f(ceiling)))
        else:
            rep.n += 1

    # 5) หมุดปี 2025 (ทั้ง 3 สาขารวมกัน — ดูจาก DB ตรง ๆ ไม่ขึ้นกับสาขาที่กำลังรัน)
    y25 = sb_get(cfg, "/rest/v1/sopo_branch_month?select=sales_tot&month=like.2025-*")
    if len(y25) >= 36:  # ครบ 12 เดือน x 3 สาขาแล้วเท่านั้นถึงเทียบหมุดได้
        rep.check("หมุด: ยอดหน้าร้านทั้งปี 2025 (3 สาขา)",
                  sum(float(r["sales_tot"] or 0) for r in y25), ANCHOR_2025_STORE, 1000.0)

    # 6) express_bill ต้องมีบิลครบเท่า DBF และยอดต้องเป็นยอดเต็ม (ต้องใช้ service key)
    # 4.5) ตารางรายวัน: ผลรวมต่อเดือนต้องเท่าตารางรายเดือนเป๊ะ (แหล่งเดียวกัน คนละ granularity)
    # ถ้าตารางยังไม่ถูกสร้าง (ยังไม่รัน sopo-app/sql/sopo_daily.sql) ข้ามเงียบ ๆ
    # cust_repeat/cust_total ไม่อยู่ในลิสต์ — นิยามรายวัน (เทียบก่อนวันนั้น) รวมแล้วไม่เท่ารายเดือน
    DAY_COLS = ("sales_tot", "online_tot", "interbranch_tot", "ret_tot", "comm_tot", "plat_tot",
                "buy_tot", "ai_tot", "bill_count", "cycle_le1_n", "cust_new", "gp_value", "gp_base",
                "po_line_total", "po_line_done")
    try:
        drows = sb_get_all(cfg, "/rest/v1/sopo_branch_day?select=day,%s&branch=eq.%s&order=day"
                           % (",".join(DAY_COLS), branch))
    except urllib.error.HTTPError:
        drows = None
    if drows is not None:
        dsum = collections.defaultdict(lambda: collections.Counter())
        for r in drows:
            for k in DAY_COLS:
                dsum[r["day"][:7]][k] += float(r[k] or 0)
        for mk, r in sorted(db.items()):
            if mk not in dsum:
                if float(r["sales_tot"] or 0) > 0:
                    rep.note_fail("%s ไม่มีแถวใน sopo_branch_day" % mk, "รายเดือนมี %s" % _f(float(r["sales_tot"] or 0)))
                continue
            chk = rep.check_live if mk == live_mk else rep.check
            for k in DAY_COLS:
                chk("%s รายวันรวม = รายเดือน (%s)" % (mk, k), dsum[mk][k], float(r.get(k) or 0), MONEY_TOL)
        # order ต้องเป็น unique key เต็ม (day,slmcod) — เรียงแค่ day แล้วแถววันเดียวกันสลับ
        # ข้ามหน้า pagination ได้ ทำให้นับซ้ำ/หลุดแบบสุ่ม (เจอมาแล้ว: BK +1,554 / SKN -32,514)
        pdrows = sb_get_all(cfg, "/rest/v1/sopo_person_day?select=day,net_sales,gp_base&branch=eq.%s&order=day,slmcod" % branch)
        pdsum = collections.defaultdict(lambda: collections.Counter())
        for r in pdrows:
            pdsum[r["day"][:7]]["net_sales"] += float(r["net_sales"] or 0)
            pdsum[r["day"][:7]]["gp_base"] += float(r["gp_base"] or 0)
        for mk, p in sorted(pm.items()):
            if mk not in pdsum:
                if p["net_sales"] > 0:
                    rep.note_fail("%s ไม่มีแถวใน sopo_person_day" % mk, "รายเดือนมี %s" % _f(p["net_sales"]))
                continue
            chk = rep.check_live if mk == live_mk else rep.check
            chk("%s person รายวันรวม = รายเดือน (net_sales)" % mk, pdsum[mk]["net_sales"], p["net_sales"], MONEY_TOL)
            chk("%s person รายวันรวม = รายเดือน (gp_base)" % mk, pdsum[mk]["gp_base"], p["gp_base"], MONEY_TOL)

    # express_sync ทำงานชั่วโมงละครั้ง จึงตามหลัง DBF ได้ตามปกติ — ฟ้องเฉพาะตอนที่เกินจริง
    # (บิลใน DB มากกว่าที่มีอยู่จริง) หรือขาดเกิน 10% (sync ค้าง/ไม่มี service key มานาน)
    # ตัดแถวที่ iv ไม่ขึ้นต้น IV/HS ออก — มีแถวปลอม 1 แถวจาก import เดิม (SKN iv='SO6904795'
    # ใส่เลข SO แทนเพราะยังไม่ออกใบกำกับ) ซึ่งไม่มีคู่ใน ARTRN โดยธรรมชาติ ไม่ใช่ sync พัง
    if svc:
        n_eb = sb_count(cfg, "/rest/v1/express_bill?select=id&branch=eq.%s&or=(iv.like.IV*,iv.like.HS*)" % branch, key=svc)
        rep.check_live("express_bill จำนวนบิล", n_eb, bills)
    rep.done()


if __name__ == "__main__":
    main()
