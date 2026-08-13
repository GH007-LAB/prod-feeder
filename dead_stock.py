# -*- coding: utf-8 -*-
"""
007 Metals - Dead Stock (ของค้างสต็อกไม่มีการขาย >90 วัน)
แทน F4_DeadStock_90d.xlsx ของระบบเก่าที่หาไฟล์ต้นทางไม่เจอใน Drive แล้ว

อ่าน STMAS.DBF (stock master) ต่อสาขา:
  - LSELDAT = วันขายล่าสุดของ SKU นั้น (null = ไม่เคยขายเลย)
  - TOTBAL  = คงเหลือปัจจุบัน (ตัด floating-point noise ใกล้ 0 ด้วย threshold 0.5)
  - TOTVAL  = มูลค่าคงเหลือปัจจุบัน
dead = TOTBAL > 0.5 AND (LSELDAT is null OR วันนี้ - LSELDAT > 90 วัน)

ล้าง+เขียนทับทั้งหมดของสาขานั้นทุกรอบ (ไม่ merge-duplicates เฉยๆ) เพราะสถานะ
"dead" กลับเป็น "active" ได้ถ้ามีคนขายออกไป ต้องลบแถวเก่าที่ไม่ dead แล้วออก

usage: python dead_stock.py <config_file> [--dry]
"""
import sys, os, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S  # reuse PROXY / read_dbf / sb_request / load_config / chunks / log

BAL_EPS = 0.5  # ตัด floating-point noise ของ TOTBAL ใกล้ 0 (เช่น 3.6e-12)
DEAD_DAYS = 90


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
    rows = []
    for r in S.read_dbf(os.path.join(src, "STMAS.DBF"),
                        fields={"STKCOD", "STKDES", "STKGRP", "TOTBAL", "TOTVAL", "LSELDAT"}):
        totbal = float(r.get("TOTBAL") or 0)
        if abs(totbal) <= BAL_EPS:
            continue
        lseldat = r.get("LSELDAT")
        days_since = (today - lseldat).days if lseldat else None
        if lseldat and days_since <= DEAD_DAYS:
            continue
        stkcod = (r.get("STKCOD") or "").strip()
        if not stkcod:
            continue
        rows.append({
            "branch": branch,
            "stkcod": stkcod,
            "stkdes": (r.get("STKDES") or "").strip(),
            "stkgrp": (r.get("STKGRP") or "").strip(),
            "totbal": totbal,
            "totval": float(r.get("TOTVAL") or 0),
            "last_sale_date": lseldat.isoformat() if lseldat else None,
            "days_since_sale": days_since,
        })

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for r in rows:
        r["synced_at"] = now_iso

    S.log("DEAD_STOCK: %d SKU ค้างสต็อก%s" % (len(rows), " (DRY)" if dry else ""))
    if dry:
        return

    # ล้างของเก่าทั้งหมดของสาขานี้ก่อน แล้วเขียนทับด้วยชุดปัจจุบัน (กัน SKU ที่หลุดเงื่อนไข dead แล้วค้างอยู่)
    S.sb_request(cfg, "DELETE", "/rest/v1/sopo_dead_stock?branch=eq.%s" % branch)
    for batch in S.chunks(rows, 200):
        S.sb_request(cfg, "POST", "/rest/v1/sopo_dead_stock?on_conflict=branch,stkcod",
                     batch, prefer="resolution=merge-duplicates,return=minimal")
    S.log("DEAD_STOCK: pushed OK")


if __name__ == "__main__":
    main()
