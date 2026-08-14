# -*- coding: utf-8 -*-
"""
007 Metals - คะแนนพัฒนาการรายเดือนต่อเซลล์ -> sopo_dev_month (ป้อน HR Merit)

ทำไมต้องมี: HR007 Merit หมวด "พัฒนาตัวเอง 15%" เดิมป้อนจากไฟล์ mentorlog_*.md ที่
build_mentor.py เขียนลง Drive — ตัดคนกลางทิ้ง: คำนวณ score+dev ที่นี่ (สูตรชุดเดียว
กับจอ Mentor: sopo-app/lib/mentor-calc.ts ซึ่งพอร์ตจาก build_mentor.py อีกที)
เขียนเข้า sopo_dev_month แล้ว hr-app อ่านผ่าน view hr_dev_score (join กับ
sopo_slmcod_map เพื่อแปลง (branch,slmcod) -> employee_id)

รันครั้งเดียวต่อรอบจาก run_all.sh (อ่านจาก Supabase ล้วน ไม่แตะ DBF — เร็วมาก)
นับเฉพาะเดือนที่ปิดรอบแล้ว (ตัดเดือนปัจจุบัน) เหมือน mentor

usage: python3 dev_score.py <config_file> [--dry]
"""
import sys, os, json, datetime, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S

# ---- สูตรแกน/คะแนน — ตรงกับ sopo-app/lib/dashboard-calc.ts + mentor-calc.ts ----
CLOSE_TGT = 1_580_000.0
LEAD_TGT = 5_270_000.0


def clamp(x, a, b):
    return max(a, min(b, x))


def score_of(r):
    net = float(r["net_sales"] or 0)
    bills = int(r["bill_count"] or 0)
    gp_base = float(r["gp_base"] or 0)
    gp_pct = (float(r["gp_value"] or 0) / gp_base * 100) if gp_base > 0 else 0
    avg_bill = net / bills if bills else 0
    win = min(round(net / CLOSE_TGT * 100), 100)
    gp = min(max(gp_pct, 0) / 45 * 100, 100)
    target = clamp(net / CLOSE_TGT * 100, 0, 100)
    dealsize = clamp(avg_bill / 12000.0 * 100, 0, 100)
    activity = min(round(bills / 1.5), 100)
    retpct = (float(r["return_value"] or 0) / net * 100) if net > 0 else 0
    ret = max(0, 100 - retpct * 20)
    return round(0.22 * win + 0.18 * gp + 0.22 * target + 0.14 * dealsize + 0.10 * activity + 0.14 * ret)


def dev_sales(sc, delta, cmean):
    return int(clamp(round(55 + 0.6 * (sc - cmean) + 0.7 * clamp(delta if delta is not None else 0, -10, 15)), 18, 95))


def sb_get(cfg, path):
    req = urllib.request.Request(cfg["SUPABASE_URL"].rstrip("/") + path, headers={
        "apikey": cfg["SUPABASE_KEY"], "Authorization": "Bearer " + cfg["SUPABASE_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = S.load_config(sys.argv[1])
    dry = ("--dry" in sys.argv) or not cfg.get("SUPABASE_URL") or not cfg.get("SUPABASE_KEY")

    # role=sales เท่านั้น — ทำเนียบอยู่ในไฟล์ salesperson_map.json ข้างจอ SOPO เดิม
    # (sopo-app มีสำเนาเดียวกัน) ถ้าไม่มีไฟล์ให้ถือว่าทุกรหัสเป็นเซลล์
    sales_ok = None
    for p in ("/Users/cto007/sopo-app/lib/salesperson_map.json",):
        if os.path.exists(p):
            m = json.load(open(p, encoding="utf-8"))
            sales_ok = {(b, c) for b, codes in m.items() for c, info in codes.items()
                        if isinstance(info, dict) and info.get("role") == "sales"}
            break

    rows = sb_get(cfg, "/rest/v1/sopo_person_month?select=branch,slmcod,month,net_sales,"
                       "bill_count,return_value,gp_value,gp_base&order=month")
    now_mk = datetime.date.today().strftime("%Y-%m")
    rows = [r for r in rows if r["month"] != now_mk
            and (sales_ok is None or (r["branch"], r["slmcod"]) in sales_ok)]

    # ค่าเฉลี่ยทีมต่อเดือน (CMEAN) จาก score ของทุกเซลล์ทุกสาขาในเดือนนั้น
    scores = {(r["branch"], r["slmcod"], r["month"]): score_of(r) for r in rows}
    months = sorted({r["month"] for r in rows})
    cmean = {}
    for mk in months:
        v = [sc for (b, c, m), sc in scores.items() if m == mk]
        cmean[mk] = sum(v) / len(v) if v else 58.0

    out = []
    people = sorted({(r["branch"], r["slmcod"]) for r in rows})
    for b, c in people:
        prev = None
        for mk in months:
            sc = scores.get((b, c, mk))
            if sc is None:
                continue
            delta = None if prev is None else sc - prev
            out.append({"branch": b, "slmcod": c, "month": mk,
                        "score": sc, "dev": dev_sales(sc, delta, cmean[mk])})
            prev = sc

    S.log("DEV: %d คน x เดือน = %d แถว (เดือนปิดรอบ %d)%s"
          % (len(people), len(out), len(months), " (DRY)" if dry else ""))
    if dry or not out:
        return
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for batch in S.chunks([dict(x, synced_at=now_iso) for x in out], 300):
        S.sb_request(cfg, "POST", "/rest/v1/sopo_dev_month?on_conflict=branch,slmcod,month",
                     batch, prefer="resolution=merge-duplicates,return=minimal")
    S.log("DEV: pushed OK")


if __name__ == "__main__":
    main()
