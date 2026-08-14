# -*- coding: utf-8 -*-
"""
007 Metals - SOPO Dashboard monthly aggregates (Phase 2a)
อ่าน ARTRN.DBF (RECTYP: 0=AI มัดจำ, 1=HS ขายเงินสด, 3=IV ใบกำกับ, 5=SR คืนสินค้า)
+ so_live ที่ prod-feeder sync ไว้แล้ว (Supabase) -> คำนวณสรุปรายเดือนต่อสาขา/ต่อเซลล์
เขียนเข้า sopo_branch_month / sopo_person_month (ดู sopo-app/sql/sopo_dashboard.sql)

ระดับเดือนเท่านั้น (v1) — ไม่ทำ week/day/custom-range ยังไม่ทำ commission/platform-fee
sub-split ของ SR (รวมเป็น ret_tot เดียว), ไม่ทำ dedup lead-axis แบบ jaccard (ใช้
so_value ตรงๆ เป็นตัวแทนง่ายๆ) — ตามที่ตกลงไว้

usage: python sopo_month.py <config_file> [--dry]
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

    branch_m = {}  # month -> dict
    day_m = {}     # month -> {"วันที่": ยอดหน้าร้าน+ออนไลน์ของวันนั้น}
    def bm(mk):
        return branch_m.setdefault(mk, {
            "sales_tot": 0.0, "online_tot": 0.0, "interbranch_tot": 0.0,
            "ret_tot": 0.0, "ai_tot": 0.0, "ai_cnt": 0,
            "so_issued_value": 0.0, "open_value": 0.0, "open_count": 0,
            "cycle_days_sum": 0.0, "cycle_days_n": 0,
        })

    for sn, r in so_by_num.items():
        sodat = r.get("sodat")
        if not sodat:
            continue
        d = datetime.date.fromisoformat(sodat)
        mk = month_key(d)
        if mk not in keep_months:
            continue
        v = float(r.get("netamt") or 0)
        b = bm(mk)
        b["so_issued_value"] += v
        if (r.get("docstat") or "").strip() != "M" and v > 0:
            b["open_value"] += v
            b["open_count"] += 1

    # ---- ชื่อลูกค้า (ARMAS) สำหรับแยก segment (online/interbranch/regular) ----
    names = {}
    for r in S.read_dbf(os.path.join(src, "ARMAS.DBF"), fields={"CUSCOD", "PRENAM", "CUSNAM"}):
        names[r.get("CUSCOD", "")] = (r.get("PRENAM", "") + " " + r.get("CUSNAM", "")).strip()

    person_m = {}  # (slmcod, month) -> dict
    def pm(sc, mk):
        return person_m.setdefault((sc, mk), {
            "net_sales": 0.0, "bill_count": 0, "big_deal_value": 0.0, "big_deal_count": 0,
            "return_value": 0.0, "so_value": 0.0, "gp_value": 0.0, "gp_base": 0.0,
        })

    for r in S.read_dbf(os.path.join(src, "ARTRN.DBF"),
                        fields={"RECTYP", "DOCNUM", "DOCDAT", "SONUM", "SLMCOD", "CUSCOD",
                                "NETAMT", "ADVAMT"}):
        dat = r.get("DOCDAT")
        if not dat or dat < cutoff:
            continue
        mk = month_key(dat)
        if mk not in keep_months:
            continue
        rectyp = (r.get("RECTYP") or "").strip()
        v = float(r.get("NETAMT") or 0)
        cc = (r.get("CUSCOD") or "").strip()
        sc = (r.get("SLMCOD") or "").strip()
        b = bm(mk)

        if rectyp == "0":  # AI - เงินมัดจำ
            b["ai_tot"] += v
            b["ai_cnt"] += 1
        elif rectyp == "5":  # SR - คืนสินค้า/ลดหนี้
            b["ret_tot"] += v
            if sc:
                pm(sc, mk)["return_value"] += v
        elif rectyp in ("1", "3"):  # HS ขายเงินสด / IV ใบกำกับ = ยอดขายจริง
            # ยอดเต็มของบิล = NETAMT + ADVAMT — ถ้าลูกค้าวางมัดจำไว้ก่อน Express จะหักมัดจำ
            # ออกจาก NETAMT แล้ว นับแต่ NETAMT จึงได้ยอดขายต่ำกว่าจริง (1,102 บิล = 20.2M)
            # legacy build_all.py ใช้ NETAMT+ADVAMT มาตลอด ("NETAMT+ADVAMT=ยอดเต็ม")
            v += float(r.get("ADVAMT") or 0)
            seg = segment(cc, names.get(cc, ""))
            if seg == "interbranch":
                # ขายระหว่างสาขาไม่ใช่ยอดขาย — legacy แยกเป็น IVIB ไม่รวมยอดหน้าร้าน
                # (เดิมบวกเข้า sales_tot ทำให้ยอดทุกหน้าจอเกินจริงปีละหลายสิบล้าน)
                b["interbranch_tot"] += v
            else:
                # ยอดรายวัน (หน้าร้าน+ออนไลน์) = ตัวเดียวกับ _tot[b][0]+_tot[b][1] ของ legacy
                # ใช้ทำการ์ด "วันนี้ทำได้" + หาเพซจากวันล่าสุดที่มีบิลจริง
                dk = str(dat.day)
                dm = day_m.setdefault(mk, {})
                dm[dk] = round(dm.get(dk, 0.0) + v, 2)
                if seg == "online":
                    b["online_tot"] += v
                else:
                    b["sales_tot"] += v
                    if sc:
                        p = pm(sc, mk)
                        p["net_sales"] += v
                        p["bill_count"] += 1
                        if v >= 100000:
                            p["big_deal_value"] += v
                            p["big_deal_count"] += 1
            # cycle time: SO->IV/HS (ใช้ทุก segment รวมออนไลน์/ระหว่างสาขาด้วย เหมือน legacy)
            sn = (r.get("SONUM") or "").strip()
            so = so_by_num.get(sn)
            if so and so.get("sodat"):
                cd = (dat - datetime.date.fromisoformat(so["sodat"])).days
                if cd >= 0:
                    b["cycle_days_sum"] += cd
                    b["cycle_days_n"] += 1

    # GP% จริง — STCRD.DBF (stock ledger): XUNITPR = ต้นทุนต่อหน่วย ณ เวลาขาย
    # (ยืนยันจากข้อมูลจริงแล้ว — LOTVAL/LUNITPR ที่คิดว่าจะใช้ได้ กลับเป็น 0 เสมอ ไม่ได้ใช้จริง)
    # เฉพาะแถวที่ DOCNUM ขึ้นต้น IV/HS (ตรงกับนิยาม "ขายจริง" เดียวกับ ARTRN RECTYP 1/3)
    # และ XUNITPR>0 เท่านั้น (สินค้าบางตัวไม่มีต้นทุนแยก เช่น อุปกรณ์เสริมที่รวมในแผ่นหลัก — ข้ามทั้ง 2 ฝั่ง กันดันมาร์จิ้นเพี้ยน)
    for r in S.read_dbf(os.path.join(src, "STCRD.DBF"),
                        fields={"DOCNUM", "DOCDAT", "SLMCOD", "TRNQTY", "UNITPR", "TRNVAL", "XUNITPR"}):
        doc = (r.get("DOCNUM") or "").strip()
        if not (doc.startswith("IV") or doc.startswith("HS")):
            continue
        dat = r.get("DOCDAT")
        if not dat or dat < cutoff:
            continue
        mk = month_key(dat)
        if mk not in keep_months:
            continue
        xunitpr = float(r.get("XUNITPR") or 0)
        if xunitpr <= 0:
            continue
        sc = (r.get("SLMCOD") or "").strip()
        if not sc:
            continue
        qty = float(r.get("TRNQTY") or 0)
        unitpr = float(r.get("UNITPR") or 0)
        trnval = float(r.get("TRNVAL") or 0)
        p = pm(sc, mk)
        p["gp_value"] += qty * (unitpr - xunitpr)
        p["gp_base"] += trnval

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
            pm(sc, mk)["so_value"] += float(r.get("NETAMT") or 0)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    branch_batch = [dict(v, branch=branch, month=mk, synced_at=now_iso, day_tot=day_m.get(mk, {}))
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
    S.log("SOPO: pushed OK")


if __name__ == "__main__":
    main()
