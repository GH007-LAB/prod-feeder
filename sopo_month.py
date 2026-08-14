# -*- coding: utf-8 -*-
"""
007 Metals - SOPO Dashboard aggregates (รายเดือน + รายวัน)
อ่าน ARTRN.DBF (RECTYP: 0=AI มัดจำ, 1=HS ขายเงินสด, 3=IV ใบกำกับ, 5=SR คืนสินค้า)
+ so_live ที่ prod-feeder sync ไว้แล้ว (Supabase) -> คำนวณสรุปต่อสาขา/ต่อเซลล์
เขียนเข้า sopo_branch_month / sopo_person_month (sopo-app/sql/sopo_dashboard.sql)
และ sopo_branch_day / sopo_person_day (sopo-app/sql/sopo_daily.sql) ใน pass เดียวกัน
— รายวันคือตัวที่ทำให้หน้าจอเลือกช่วงเวลาได้เหมือน SOPO เดิม (เดือน/สัปดาห์/วัน/กำหนดเอง)

ยังไม่ทำ commission/platform-fee sub-split ของ SR (รวมเป็น ret_tot เดียว), ไม่ทำ
dedup lead-axis แบบ jaccard (ใช้ so_value ตรงๆ เป็นตัวแทนง่ายๆ) — ตามที่ตกลงไว้

usage: python sopo_month.py <config_file> [--dry] [--full]
   --full = push รายวันทั้ง 20 เดือน (ปกติรอบ 10 นาที push แค่เดือนนี้+เดือนก่อน
   เพราะเดือนเก่าไม่ขยับแล้ว — ครั้งแรกที่ไม่มี state marker จะ full เองอัตโนมัติ)
"""
import sys, os, json, datetime, urllib.request, urllib.error, urllib.parse, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S  # reuse PROXY / read_dbf / sb_request / load_config / state_dir / chunks / log

# 20 เดือน = ครอบคลุม ม.ค. ของปีนี้เสมอ (แม้รันเดือน ธ.ค.) + ทั้งปีที่แล้วไว้เทียบ
# เดิมตั้ง 6 ซึ่งทำให้ยอดสะสมทั้งปีของ Bonus Race ขาด ม.ค.-ก.พ. ไปเฉย ๆ (หาย 34.6M)
MONTHS_BACK = 20

# ---- กติกาแยกประเภทลูกค้า: ยกมาจาก build_all.py ของ SOPO เดิมให้ตรงกันทุกตัวอักษร ----
# ระหว่างสาขา = รหัสลูกค้าเมื่อตัดตัวอักษรนำหน้าออกแล้วขึ้นต้นด้วยเลขชุดนี้ (เลขผู้เสียภาษีของสาขาเอง)
# ตรวจแล้วว่าให้ผลตรงกับ legacy: ยอดหน้าร้านปี 2025 = 138,559,956 ตรงหมุด "138M แซงทั้งปี 2025"
_IBNUM = ("042090090", "042471", "042491", "042492", "061031")
# ออนไลน์ = รหัสขึ้นต้น O (จับได้ 100% ของยอดออนไลน์ปี 2026 ตรวจแล้ว)
# ชื่อ platform เป็น fallback สำหรับข้อมูลเก่าที่ยังไม่มีรหัส — ตามที่ legacy ทำไว้
PLATFORMS = ("SHOPEE", "TIKTOK", "LAZADA", "SHOPEEPAY", "NOCNOC")


def month_key(d):
    return "%04d-%02d" % (d.year, d.month)


def months_window(today, n):
    ks = set()
    y, m = today.year, today.month
    for _ in range(n):
        ks.add("%04d-%02d" % (y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return ks


def segment(cuscod, name):
    c = (cuscod or "").strip()
    if re.sub(r"^[^0-9]+", "", c).startswith(_IBNUM):
        return "interbranch"
    if c[:1].upper() == "O":
        return "online"
    if any(p in (name or "").upper() for p in PLATFORMS):
        return "online"
    return "regular"


def sb_get(cfg, path):
    url = cfg["SUPABASE_URL"].rstrip("/") + path
    req = urllib.request.Request(url, headers={
        "apikey": cfg["SUPABASE_KEY"],
        "Authorization": "Bearer " + cfg["SUPABASE_KEY"],
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = S.load_config(sys.argv[1])
    dry = ("--dry" in sys.argv) or not cfg.get("SUPABASE_URL") or not cfg.get("SUPABASE_KEY")
    branch = cfg["BRANCH"]
    src = cfg.get("SRC", "")
    if cfg.get("PROXY_URL"):
        S.PROXY.update(url=cfg["PROXY_URL"], token=cfg.get("PROXY_TOKEN", ""), branch=branch)

    today = datetime.date.today()
    keep_months = months_window(today, MONTHS_BACK)
    cutoff = today - datetime.timedelta(days=MONTHS_BACK * 31)

    # ---- so_live ของสาขานี้ (sync แล้วจาก so_push.py) — ใช้เป็นฝั่ง SO ทั้งหมด ----
    so_rows = sb_get(cfg, "/rest/v1/so_live?branch=eq.%s&select=sonum,sodat,netamt,docstat" % branch)
    so_by_num = {r["sonum"]: r for r in so_rows if r.get("sonum")}

    _B0 = {"sales_tot": 0.0, "online_tot": 0.0, "interbranch_tot": 0.0,
           "ret_tot": 0.0, "comm_tot": 0.0, "plat_tot": 0.0, "buy_tot": 0.0,
           "ai_tot": 0.0, "ai_cnt": 0, "bill_count": 0,
           "so_issued_value": 0.0, "open_value": 0.0, "open_count": 0,
           "cycle_days_sum": 0.0, "cycle_days_n": 0, "cycle_le1_n": 0,
           "cust_new": 0, "cust_repeat": 0, "cust_total": 0,
           "gp_value": 0.0, "gp_base": 0.0,
           "po_line_total": 0, "po_line_done": 0}
    branch_m = {}  # month -> dict
    branch_d = {}  # date  -> dict (คอลัมน์ชุดเดียวกัน แค่ granularity = วัน)
    day_m = {}     # month -> {"วันที่": ยอดหน้าร้าน+ออนไลน์ของวันนั้น}
    def bm(mk):
        return branch_m.setdefault(mk, dict(_B0))
    def bd(d):
        return branch_d.setdefault(d, dict(_B0))

    for sn, r in so_by_num.items():
        sodat = r.get("sodat")
        if not sodat:
            continue
        d = datetime.date.fromisoformat(sodat)
        mk = month_key(d)
        if mk not in keep_months:
            continue
        v = float(r.get("netamt") or 0)
        for b in (bm(mk), bd(d)):
            b["so_issued_value"] += v
            if (r.get("docstat") or "").strip() != "M" and v > 0:
                b["open_value"] += v
                b["open_count"] += 1

    # ---- ชื่อลูกค้า (ARMAS) สำหรับแยก segment (online/interbranch/regular) ----
    names = {}
    for r in S.read_dbf(os.path.join(src, "ARMAS.DBF"), fields={"CUSCOD", "PRENAM", "CUSNAM"}):
        names[r.get("CUSCOD", "")] = (r.get("PRENAM", "") + " " + r.get("CUSNAM", "")).strip()

    _P0 = {"net_sales": 0.0, "bill_count": 0, "big_deal_value": 0.0, "big_deal_count": 0,
           "return_value": 0.0, "comm_value": 0.0, "comm_count": 0,
           "so_value": 0.0, "so_count": 0, "gp_value": 0.0, "gp_base": 0.0}
    # sale_docs: บิลขาย (หน้าร้าน+ออนไลน์ ไม่รวมระหว่างสาขา) -> (month, date, เป็นหน้าร้านไหม)
    #   หน้าร้านใช้จำกัด scope GP · ทั้งสองใช้ทำลิสต์ขายดี (legacy DET รวมออนไลน์ด้วย)
    # sr_docs: เอกสาร SR -> (month, date, slmcod, ชื่อลูกค้า upper) — แยกก้อนที่ระดับบรรทัดใน STCRD
    sale_docs, sr_docs = {}, {}
    person_m = {}  # (slmcod, month) -> dict
    person_d = {}  # (slmcod, date)  -> dict
    def pm(sc, mk):
        return person_m.setdefault((sc, mk), dict(_P0))
    def pd(sc, d):
        return person_d.setdefault((sc, d), dict(_P0))

    # ---- ลูกค้าใหม่/ซื้อซ้ำ: ต้องรู้ "ซื้อครั้งแรกเมื่อไหร่" จากทั้งประวัติ ไม่ใช่แค่ใน window
    # ตัดลูกค้าเงินสดออกเหมือน legacy (_mcust ตัด 'เงินสด'/'-'/ว่าง) — ของเราตัดจากชื่อ ARMAS
    def is_cash(cc, nm):
        n = (nm or "").replace("\xa0", " ").strip()
        return (not cc) or ("เงินสด" in cc) or ("เงินสด" in n) or n in ("สด", "ลูกค้า สด", "-", "")
    cust_first = {}   # cuscod -> วันที่ซื้อหน้าร้านครั้งแรก (ทั้งประวัติ)
    cust_by_mk = {}   # month -> set(cuscod)
    cust_by_day = {}  # date  -> set(cuscod)

    for r in S.read_dbf(os.path.join(src, "ARTRN.DBF"),
                        fields={"RECTYP", "DOCNUM", "DOCDAT", "SONUM", "SLMCOD", "CUSCOD",
                                "NETAMT", "ADVAMT", "DOCSTAT"}):
        dat = r.get("DOCDAT")
        if not dat:
            continue
        rectyp = (r.get("RECTYP") or "").strip()
        if (r.get("DOCSTAT") or "").strip() == "C":
            continue  # เอกสารยกเลิก (NETAMT=0 อยู่แล้ว แต่ห้ามให้ปนเข้า bill_count/ลูกค้า)
        cc = (r.get("CUSCOD") or "").strip()
        seg = segment(cc, names.get(cc, "")) if rectyp in ("1", "3") else ""

        # first-seen ของลูกค้า: ดูทั้งประวัติ "ก่อน" ตัดหน้าต่างเวลา — ไม่งั้นลูกค้าเก่าปี 67
        # จะกลายเป็น "ลูกค้าใหม่" ของเดือนแรกใน window
        if seg == "regular" and not is_cash(cc, names.get(cc, "")):
            if cc not in cust_first or dat < cust_first[cc]:
                cust_first[cc] = dat

        if dat < cutoff:
            continue
        mk = month_key(dat)
        if mk not in keep_months:
            continue
        v = float(r.get("NETAMT") or 0)
        sc = (r.get("SLMCOD") or "").strip()
        bb = (bm(mk), bd(dat))  # ยิงทั้งถังรายเดือนและรายวันพร้อมกัน — นิยามชุดเดียว

        if rectyp == "0":  # AI - เงินมัดจำ
            for b in bb:
                b["ai_tot"] += v
                b["ai_cnt"] += 1
        elif rectyp == "5":  # SR — แยก คืนจริง/%ช่าง/แพลตฟอร์ม ที่ระดับบรรทัดใน STCRD (ด้านล่าง)
            sr_docs[(r.get("DOCNUM") or "").strip()] = (mk, dat, sc, (names.get(cc, "") or "").upper())
        elif rectyp in ("1", "3"):  # HS ขายเงินสด / IV ใบกำกับ = ยอดขายจริง
            # ยอดเต็มของบิล = NETAMT + ADVAMT — ถ้าลูกค้าวางมัดจำไว้ก่อน Express จะหักมัดจำ
            # ออกจาก NETAMT แล้ว นับแต่ NETAMT จึงได้ยอดขายต่ำกว่าจริง (1,102 บิล = 20.2M)
            # legacy build_all.py ใช้ NETAMT+ADVAMT มาตลอด ("NETAMT+ADVAMT=ยอดเต็ม")
            v += float(r.get("ADVAMT") or 0)
            doc = (r.get("DOCNUM") or "").strip()
            if seg == "interbranch":
                # ขายระหว่างสาขาไม่ใช่ยอดขาย — legacy แยกเป็น IVIB ไม่รวมยอดหน้าร้าน
                for b in bb:
                    b["interbranch_tot"] += v
            else:
                # ยอดรายวัน (หน้าร้าน+ออนไลน์) = ตัวเดียวกับ _tot[b][0]+_tot[b][1] ของ legacy
                dk = str(dat.day)
                dm = day_m.setdefault(mk, {})
                dm[dk] = round(dm.get(dk, 0.0) + v, 2)
                sale_docs[doc] = (mk, dat, seg == "regular")
                if seg == "online":
                    for b in bb:
                        b["online_tot"] += v
                else:
                    for b in bb:
                        b["sales_tot"] += v
                        b["bill_count"] += 1
                    if not is_cash(cc, names.get(cc, "")):
                        cust_by_mk.setdefault(mk, set()).add(cc)
                        cust_by_day.setdefault(dat, set()).add(cc)
                    if sc:
                        for p in (pm(sc, mk), pd(sc, dat)):
                            p["net_sales"] += v
                            p["bill_count"] += 1
                            if v >= 100000:
                                p["big_deal_value"] += v
                                p["big_deal_count"] += 1
                    # cycle time SO->IV/HS: legacy คิดเฉพาะบิลหน้าร้าน (block ใน else หลังเช็ค
                    # online ของ build_all) — เดิมเข้าใจผิดว่ารวมทุก segment
                    sn = (r.get("SONUM") or "").strip()
                    so = so_by_num.get(sn)
                    if so and so.get("sodat"):
                        cd = (dat - datetime.date.fromisoformat(so["sodat"])).days
                        if cd >= 0:
                            for b in bb:
                                b["cycle_days_sum"] += cd
                                b["cycle_days_n"] += 1
                                if cd <= 1:
                                    b["cycle_le1_n"] += 1  # ตัวตั้งของ "ตอบใน 24 ชม." (resp24)

    # ---- สรุปลูกค้าใหม่/ซื้อซ้ำ (หน้าร้าน ไม่นับเงินสด) ----
    # รายเดือน: ใหม่ = เดือนแรกที่เคยซื้อ == เดือนนี้ · ซื้อซ้ำ = เคยซื้อก่อนหน้า (นิยาม legacy)
    # รายวัน: เทียบ "ก่อนวันนั้น" — ผลรวมข้ามหลายวันของ cust_total เป็นลูกค้า-วัน (นับหัวซ้ำได้)
    # แต่ cust_new รวมข้ามวันแล้วยังถูกต้อง (ลูกค้าใหม่มีวันแรกวันเดียว)
    for mk, ccs in cust_by_mk.items():
        b = bm(mk)
        b["cust_total"] = len(ccs)
        b["cust_new"] = sum(1 for c in ccs if month_key(cust_first[c]) == mk)
        b["cust_repeat"] = sum(1 for c in ccs if cust_first[c] < datetime.date(int(mk[:4]), int(mk[5:7]), 1))
    for d, ccs in cust_by_day.items():
        b = bd(d)
        b["cust_total"] = len(ccs)
        b["cust_new"] = sum(1 for c in ccs if cust_first[c] == d)
        b["cust_repeat"] = sum(1 for c in ccs if cust_first[c] < d)

    # GP% จริง — STCRD.DBF (stock ledger): XUNITPR = ต้นทุนต่อหน่วย ณ เวลาขาย
    # (ยืนยันจากข้อมูลจริงแล้ว — LOTVAL/LUNITPR ที่คิดว่าจะใช้ได้ กลับเป็น 0 เสมอ ไม่ได้ใช้จริง)
    # นับเฉพาะบิลที่อยู่ใน store_docs = บิลหน้าร้านชุดเดียวกับที่นับเป็น sales_tot
    # (เดิมกรองแค่ DOCNUM ขึ้นต้น IV/HS จึงรวมบิลออนไลน์กับบิลระหว่างสาขาเข้ามาด้วย
    #  กลายเป็นเอากำไรจากบิลชุดหนึ่งไปหารกับยอดขายอีกชุด GP% เลยเพี้ยน — legacy กำกับไว้ใน
    #  _sbc_gp ว่า "ตัด online O + interbranch C/J — scope เดียวกับ salesTot")
    # และ XUNITPR>0 เท่านั้น (สินค้าบางตัวไม่มีต้นทุนแยก เช่น อุปกรณ์เสริมที่รวมในแผ่นหลัก — ข้ามทั้ง 2 ฝั่ง กันดันมาร์จิ้นเพี้ยน)
    # month มาจาก store_docs ไม่ใช่ DOCDAT ของ STCRD — กัน GP ไปตกคนละเดือนกับยอดขายบิลเดียวกัน
    # ---- ลิสต์ขายดี: parse ความหนา 0.xx จากชื่อสินค้า (กติกาเดียวกับ legacy cb/cb2) ----
    _thick = re.compile(r"^0\.\d{2}$")
    CB2_PREFIX = ("01WP", "01WC", "01P3", "01P5")
    coil_m, coil2_m, coil_d, coil2_d = {}, {}, {}, {}
    def coil_label(desc):
        toks = re.split(r"[\s\xa0]+", desc or "")
        for i, t in enumerate(toks):
            if _thick.match(t):
                return "%s %s %s" % (toks[i - 1] if i >= 1 else "?", t,
                                     toks[i + 1] if i + 1 < len(toks) else "?")
        return None

    # สะสมไว้ทำ ANNOUNCE ของ Monday Brief (sopo-app/sql/sopo_announce.sql):
    #   risk: ยอดขาย 30 วันล่าสุดต่อ SKU -> เทียบสต็อกคงเหลือ = สัปดาห์ที่ของพอขาย
    #   rrp:  ราคาสั่งซื้อ (บรรทัด PO — แหล่งเดียวกับ legacy price[]) ต่อ SKU ต่อเดือน
    #         เทียบเดือนล่าสุดกับเดือนก่อน = ราคาขยับ (RR รับเข้าเบาบางเกิน บางเดือนไม่พอเทียบ)
    today30 = today - datetime.timedelta(days=30)
    risk = {}     # stkcod -> [qty30, val30, ชื่อ]
    rrp = {}      # (month, stkcod) -> [qty, amt]
    rr_name = {}

    # STCRD (stock ledger) รอบเดียว ใช้ 4 งาน — บรรทัดสินค้าของทุกเอกสารอยู่ที่นี่:
    #   IV/HS หน้าร้าน -> GP (scope เดียวกับ sales_tot ตาม _sbc_gp ของ legacy)
    #   IV/HS หน้าร้าน+ออนไลน์ -> ลิสต์ขายดีคอยล์ (legacy DET รวมออนไลน์)
    #   SR -> แยก คืนจริง/%ช่าง/แพลตฟอร์ม ที่ระดับบรรทัด (กติกา build_all.py:490-497)
    #   RR -> ยอดรับซื้อเข้า (BUYVAL ของจอเดิม)
    for r in S.read_dbf(os.path.join(src, "STCRD.DBF"),
                        fields={"DOCNUM", "DOCDAT", "SLMCOD", "TRNQTY", "UNITPR",
                                "TRNVAL", "XUNITPR", "STKCOD", "STKDES"}):
        doc = (r.get("DOCNUM") or "").strip()
        trnval = float(r.get("TRNVAL") or 0)

        if doc.startswith("RR"):  # รับของเข้าสต็อก = ยอดรับซื้อ
            dat = r.get("DOCDAT")
            if not dat or dat < cutoff:
                continue
            mk = month_key(dat)
            if mk in keep_months:
                bm(mk)["buy_tot"] += trnval
                bd(dat)["buy_tot"] += trnval
            continue

        sr = sr_docs.get(doc)
        if sr is not None:  # บรรทัดของเอกสาร SR — แยกก้อนตามชนิดบรรทัด
            if trnval <= 0:
                continue
            mk, dat, sc, cn = sr
            code = (r.get("STKCOD") or "").strip().upper()
            desc = (r.get("STKDES") or "").replace("\xa0", " ")
            iscomm = (code.startswith(("07COMM", "07COMI")) or "เปอร์เซ็นต์ช่าง" in desc
                      or "ผู้รับเหมา" in desc or "คอมมิช" in desc)
            if iscomm:
                isplat = ("ONLINE" in code or "REFUND" in code or "แพล" in desc
                          or any(p in cn for p in PLATFORMS))
                key = "plat_tot" if isplat else "comm_tot"
                bm(mk)[key] += trnval
                bd(dat)[key] += trnval
                if sc and not isplat:  # %ช่างรายคน (การ์ดรายคนบน leaderboard เดิม)
                    for p in (pm(sc, mk), pd(sc, dat)):
                        p["comm_value"] += trnval
                        p["comm_count"] += 1
            else:
                bm(mk)["ret_tot"] += trnval
                bd(dat)["ret_tot"] += trnval
                if sc:
                    pm(sc, mk)["return_value"] += trnval
                    pd(sc, dat)["return_value"] += trnval
            continue

        hit = sale_docs.get(doc)
        if hit is None:
            continue
        mk, dat, is_store = hit

        stk = (r.get("STKCOD") or "").strip().upper()
        # อัตราขาย 30 วันล่าสุด (ทุกช่องทางที่ดึงสต็อกจริง) — ไม่รวมคอยล์วัตถุดิบ ZZ
        if dat >= today30 and stk and not stk.startswith("ZZ"):
            a = risk.setdefault(stk, [0.0, 0.0, ""])
            a[0] += float(r.get("TRNQTY") or 0)
            a[1] += trnval
            if not a[2]:
                a[2] = (r.get("STKDES") or "").strip()
        if stk.startswith("01"):
            lab = coil_label(r.get("STKDES"))
            if lab:
                cm, cd_ = (coil2_m, coil2_d) if stk.startswith(CB2_PREFIX) else (coil_m, coil_d)
                qd = cm.setdefault(mk, {})
                qd[lab] = round(qd.get(lab, 0.0) + float(r.get("TRNQTY") or 0), 2)
                qd = cd_.setdefault(dat, {})
                qd[lab] = round(qd.get(lab, 0.0) + float(r.get("TRNQTY") or 0), 2)

        if not is_store:
            continue
        xunitpr = float(r.get("XUNITPR") or 0)
        if xunitpr <= 0:
            continue
        qty = float(r.get("TRNQTY") or 0)
        unitpr = float(r.get("UNITPR") or 0)
        gpv = qty * (unitpr - xunitpr)
        # ระดับสาขา: รวมทุกบรรทัด (บิลไม่มีรหัสเซลล์ก็นับ) — ตัวเลข "กำไรขั้นต้น" ของ KPI
        bm(mk)["gp_value"] += gpv
        bm(mk)["gp_base"] += trnval
        bd(dat)["gp_value"] += gpv
        bd(dat)["gp_base"] += trnval
        sc = (r.get("SLMCOD") or "").strip()
        if sc:
            for p in (pm(sc, mk), pd(sc, dat)):
                p["gp_value"] += gpv
                p["gp_base"] += trnval

    # ---- สินค้าเสี่ยงขาด: คงเหลือ (STMAS) ÷ อัตราขาย/สัปดาห์ = สัปดาห์ที่ของพอ ----
    # เกณฑ์ธงตาม build_buy.py:64 (🔴 <2 · 🟡 <4) เก็บเฉพาะ <12 สัปดาห์ (actionable)
    onhand = {}
    for r in S.read_dbf(os.path.join(src, "STMAS.DBF"), fields={"STKCOD", "TOTBAL", "STKDES"}):
        onhand[(r.get("STKCOD") or "").strip().upper()] = (
            float(r.get("TOTBAL") or 0), (r.get("STKDES") or "").strip())
    risk_rows = []
    for stk, (q30, v30, nm) in risk.items():
        if q30 <= 0:
            continue
        oh, nm2 = onhand.get(stk, (0.0, ""))
        spw = q30 / 30.0 * 7.0
        wv = round(oh / spw, 1) if spw > 0 and oh >= 0 else None
        if wv is None or wv >= 12:
            continue
        risk_rows.append({
            "branch": branch, "stkcod": stk, "stkdes": nm or nm2,
            "onhand": round(oh, 2), "sale30_qty": round(q30, 2), "sale30_val": round(v30, 2),
            "weeks_cover": wv,
            "flag": "🔴 เสี่ยงขาด" if wv < 2 else ("🟡 ใกล้หมด" if wv < 4 else "🟢 พอ"),
        })

    # ---- %รับของ PO (ful ของจอเดิม): บรรทัด PO ที่รับครบแล้ว / บรรทัดทั้งหมดของเดือน ----
    # นับที่ระดับบรรทัด (POPRIT) ตาม POdone/POcnt ของ build_buy — REMQTY<=0 = รับครบ
    # ค่าเป็น "สถานะปัจจุบัน" ของ PO ที่ออกเดือนนั้น (รับของทีหลังตัวเลขเดือนเก่าขยับขึ้นได้
    # — ตรงกับ note จอเดิม "สถานะรับของทั้งเดือน")
    if S._file_exists(os.path.join(src, "POPR.DBF")):
        po_mk = {}  # PONUM -> (month, date)
        for r in S.read_dbf(os.path.join(src, "POPR.DBF"), fields={"PONUM", "PODAT"}):
            pod = r.get("PODAT")
            if not pod or pod < cutoff:
                continue
            pmk = month_key(pod)
            if pmk in keep_months:
                po_mk[(r.get("PONUM") or "").strip()] = (pmk, pod)
        for r in S.read_dbf(os.path.join(src, "POPRIT.DBF"),
                            fields={"PONUM", "REMQTY", "STKCOD", "STKDES", "ORDQTY", "TRNVAL"}):
            hit = po_mk.get((r.get("PONUM") or "").strip())
            if hit is None:
                continue
            pmk, pod = hit
            done = 1 if float(r.get("REMQTY") or 0) <= 0 else 0
            for b in (bm(pmk), bd(pod)):
                b["po_line_total"] += 1
                b["po_line_done"] += done
            # ราคาสั่งซื้อต่อ SKU ต่อเดือน (legacy price[]: qty=คอลัมน์จำนวน, amt=มูลค่าบรรทัด)
            stk = (r.get("STKCOD") or "").strip().upper()
            if stk:
                a = rrp.setdefault((pmk, stk), [0.0, 0.0])
                a[0] += float(r.get("ORDQTY") or 0)
                a[1] += float(r.get("TRNVAL") or 0)
                if stk not in rr_name:
                    rr_name[stk] = (r.get("STKDES") or "").strip()

    # ---- ราคาซื้อขยับ: เดือนล่าสุดที่มีสั่งซื้อ vs เดือนก่อนหน้า (top 250 ตามมูลค่า — legacy) ----
    price_rows = []
    pm_keys = sorted({k[0] for k in rrp})
    if pm_keys:
        curM = pm_keys[-1]
        prevM = pm_keys[-2] if len(pm_keys) >= 2 else curM
        cur_amt = {s: v[1] for (m2, s), v in rrp.items() if m2 == curM}
        for s in sorted(cur_amt, key=lambda k: -cur_amt[k])[:250]:
            cq, ca = rrp.get((curM, s), [0.0, 0.0])
            pq, pa = rrp.get((prevM, s), [0.0, 0.0])
            price_rows.append({
                "branch": branch, "stkcod": s, "stkdes": rr_name.get(s, ""),
                "prev_month": prevM, "cur_month": curM,
                "prev_qty": round(pq, 2), "prev_amt": round(pa, 2),
                "cur_qty": round(cq, 2), "cur_amt": round(ca, 2),
            })

    # so_value ต่อเซลล์ (lead-axis proxy แบบง่าย = มูลค่า SO ที่ SLMCOD คนนั้นออกเดือนนี้)
    for r in S.read_dbf(os.path.join(src, "OESO.DBF"), fields={"SODAT", "SLMCOD", "NETAMT"}):
        sod = r.get("SODAT")
        if not sod:
            continue
        mk = month_key(sod)
        if mk not in keep_months:
            continue
        sc = (r.get("SLMCOD") or "").strip()
        if sc:
            v = float(r.get("NETAMT") or 0)
            for p in (pm(sc, mk), pd(sc, sod)):
                p["so_value"] += v
                p["so_count"] += 1  # "หว่าน N ใบ" ของจดหมายโค้ช (leadcnt เดิม)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    branch_batch = [dict(v, branch=branch, month=mk, synced_at=now_iso, day_tot=day_m.get(mk, {}),
                         coil_top=coil_m.get(mk, {}), coil_top2=coil2_m.get(mk, {}))
                    for mk, v in branch_m.items()]
    person_batch = [dict(v, branch=branch, slmcod=sc, month=mk, synced_at=now_iso) for (sc, mk), v in person_m.items()]

    S.log("SOPO: %d branch-month rows, %d person-month rows%s" %
          (len(branch_batch), len(person_batch), " (DRY)" if dry else ""))
    if dry or (not branch_batch and not person_batch):
        return

    for batch in S.chunks(branch_batch, 100):
        try:
            S.sb_request(cfg, "POST", "/rest/v1/sopo_branch_month?on_conflict=branch,month",
                         batch, prefer="resolution=merge-duplicates,return=minimal")
        except RuntimeError as e:
            # ยังไม่ได้รัน sopo-app/sql/sopo_branch_month_day_tot.sql -> ยังไม่มีคอลัมน์ day_tot
            # ส่งใหม่แบบไม่มียอดรายวัน ดีกว่าปล่อยให้ทั้งรอบล้ม (การ์ด "วันนี้ทำได้" จะยังว่างไว้ก่อน)
            if "day_tot" not in str(e):
                raise
            S.log("SOPO: ไม่มีคอลัมน์ day_tot -> push แบบไม่มียอดรายวัน (รัน sql/sopo_branch_month_day_tot.sql ก่อน)")
            S.sb_request(cfg, "POST", "/rest/v1/sopo_branch_month?on_conflict=branch,month",
                         [{k: x for k, x in row.items() if k != "day_tot"} for row in batch],
                         prefer="resolution=merge-duplicates,return=minimal")
    for batch in S.chunks(person_batch, 200):
        S.sb_request(cfg, "POST", "/rest/v1/sopo_person_month?on_conflict=branch,slmcod,month",
                     batch, prefer="resolution=merge-duplicates,return=minimal")

    # ---- ANNOUNCE (snapshot ปัจจุบัน): ลบของสาขาแล้วเขียนทับ — SKU ที่พ้นเงื่อนไขหายเอง ----
    try:
        S.sb_request(cfg, "DELETE", "/rest/v1/sopo_stock_risk?branch=eq.%s" % branch)
        for batch in S.chunks([dict(x, synced_at=now_iso) for x in risk_rows], 200):
            S.sb_request(cfg, "POST", "/rest/v1/sopo_stock_risk?on_conflict=branch,stkcod",
                         batch, prefer="resolution=merge-duplicates,return=minimal")
        S.sb_request(cfg, "DELETE", "/rest/v1/sopo_price_move?branch=eq.%s" % branch)
        for batch in S.chunks([dict(x, synced_at=now_iso) for x in price_rows], 200):
            S.sb_request(cfg, "POST", "/rest/v1/sopo_price_move?on_conflict=branch,stkcod",
                         batch, prefer="resolution=merge-duplicates,return=minimal")
        S.log("SOPO: announce %d เสี่ยงขาด + %d ราคาซื้อ" % (len(risk_rows), len(price_rows)))
    except RuntimeError as e:
        if "sopo_stock_risk" not in str(e) and "sopo_price_move" not in str(e):
            raise
        S.log("SOPO: ยังไม่มีตาราง announce -> ข้าม (รัน sopo-app/sql/sopo_announce.sql ก่อน)")

    # ---- รายวัน: sopo_branch_day / sopo_person_day ----
    # เดือนเก่าไม่ขยับแล้ว รอบปกติจึง push แค่เดือนนี้+เดือนก่อน (delete ช่วงแล้ว insert
    # ใหม่ = deterministic บิลที่โดนยกเลิกหายเองด้วย) ครั้งแรก/สั่ง --full ค่อยลงครบ 20 เดือน
    marker = os.path.join(S.state_dir(), "sopo_day_full_%s.txt" % branch)
    full = ("--full" in sys.argv) or not os.path.exists(marker)
    prev = (today.replace(day=1) - datetime.timedelta(days=1))
    recent = {month_key(today), month_key(prev)}
    pick = (lambda d: True) if full else (lambda d: month_key(d) in recent)

    day_batch = [dict(v, branch=branch, day=d.isoformat(), synced_at=now_iso,
                      coil_top=coil_d.get(d, {}), coil_top2=coil2_d.get(d, {}))
                 for d, v in branch_d.items() if pick(d)]
    pday_batch = [dict(v, branch=branch, slmcod=sc, day=d.isoformat(), synced_at=now_iso)
                  for (sc, d), v in person_d.items() if pick(d)]
    days = sorted(x["day"] for x in day_batch)
    if days:
        rng = "day=gte.%s&day=lte.%s" % (days[0], days[-1])
        try:
            S.sb_request(cfg, "DELETE", "/rest/v1/sopo_branch_day?branch=eq.%s&%s" % (branch, rng))
            for batch in S.chunks(day_batch, 300):
                S.sb_request(cfg, "POST", "/rest/v1/sopo_branch_day?on_conflict=branch,day",
                             batch, prefer="resolution=merge-duplicates,return=minimal")
            S.sb_request(cfg, "DELETE", "/rest/v1/sopo_person_day?branch=eq.%s&%s" % (branch, rng))
            for batch in S.chunks(pday_batch, 300):
                S.sb_request(cfg, "POST", "/rest/v1/sopo_person_day?on_conflict=branch,slmcod,day",
                             batch, prefer="resolution=merge-duplicates,return=minimal")
            if full:
                open(marker, "w").write(now_iso)
            S.log("SOPO: day rows %d branch + %d person%s" %
                  (len(day_batch), len(pday_batch), " (FULL)" if full else ""))
        except RuntimeError as e:
            # ตารางรายวันยังไม่ถูกสร้าง (ยังไม่รัน sql/sopo_daily.sql) — ข้ามส่วนนี้ไป
            # รายเดือนที่ push ไปแล้วไม่กระทบ หน้าจอแค่ยังเลือกช่วงวันไม่ได้
            if "sopo_branch_day" not in str(e) and "sopo_person_day" not in str(e):
                raise
            S.log("SOPO: ยังไม่มีตารางรายวัน -> ข้าม (รัน sopo-app/sql/sopo_daily.sql ก่อน)")
    S.log("SOPO: pushed OK")


if __name__ == "__main__":
    main()
