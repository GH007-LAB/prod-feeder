# -*- coding: utf-8 -*-
"""
007 Metals - เติมยอดจริงจาก Express ให้ express_bill.amount (quote-app)

ทำไมต้องมีสคริปต์นี้: สคริปต์ import เดิมของ quote-app คำนวณ amount ขึ้นมาใหม่เอง
เพราะ quote007 ไม่เก็บยอดรวมต่อบิล ผลคือยอดไม่ตรงกับ Express 25 จาก 242 ใบ
(ไล่สาเหตุแล้ว: frees[].pr ความหมายไม่คงที่ บางบรรทัดเป็นราคา/หน่วย บางบรรทัด
เป็นยอดรวมแล้ว ทำให้คูณจำนวนซ้ำสองรอบ + บางใบ Express รวม VAT + ปัดเศษท้ายบิล)
คำนวณให้เป๊ะไม่ได้ในทางหลักการ จึงดึง NETAMT จาก ARTRN.DBF ซึ่งเป็นตัวจริงมาทับ

จับคู่ด้วย (branch, DOCNUM) ไม่ใช่ DOCNUM เดี่ยว — Express เดินเลข IV แยกต่อสาขา
เลขเดียวกันมีได้หลายสาขา (ตรวจแล้ว: เลขชุดนี้ไปเจอทั้ง BK/SKN/PPS ทับกัน)
เฉพาะ RECTYP 1/3 (HS ขายเงินสด / IV ใบกำกับ) = เอกสารขายจริง

usage: SUPABASE_KEY=<service_role_key> python express_amount.py <config_file> [--dry]
   ใช้ config สาขาไหนก็ได้ — สคริปต์วนอ่าน ARTRN.DBF ครบทุกสาขาเองจาก
   cfg_<BRANCH>.txt ที่อยู่ข้างกัน

   ⚠️ ต้องใช้ service_role key (ส่งผ่าน env ไม่ต้องเขียนลงไฟล์) เพราะ express_bill
   ให้สิทธิ์อ่านแค่ authenticated + เขียนได้แค่ service role ตาม quote_schema.sql
   — anon key ที่ feeder ใช้ปกติจะอ่านได้ 0 แถว
"""
import sys, os, json, datetime, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S

BRANCHES = ("BK", "SKN", "PPS")
SALE_RECTYP = ("1", "3")  # HS ขายเงินสด / IV ใบกำกับ


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
    # env ทับ config — ให้ส่ง service_role key เข้ามาได้โดยไม่ต้องเขียนลงไฟล์
    if os.environ.get("SUPABASE_KEY"):
        cfg["SUPABASE_KEY"] = os.environ["SUPABASE_KEY"]
    dry = ("--dry" in sys.argv) or not cfg.get("SUPABASE_URL") or not cfg.get("SUPABASE_KEY")
    cfg_dir = os.path.dirname(os.path.abspath(sys.argv[1]))

    bills = sb_get(cfg, "/rest/v1/express_bill?select=id,iv,branch,amount,amount_calc,amount_source")
    want = {}
    for b in bills:
        if b.get("branch") and b.get("iv"):
            want[(b["branch"], b["iv"].strip())] = b
    S.log("EXPRESS_AMT: express_bill %d แถว (จับคู่ได้ %d)" % (len(bills), len(want)))

    real = {}
    for br in BRANCHES:
        cf = os.path.join(cfg_dir, "cfg_%s.txt" % br)
        if not os.path.exists(cf):
            S.log("EXPRESS_AMT: ข้าม %s (ไม่มี %s)" % (br, cf))
            continue
        bcfg = S.load_config(cf)
        if bcfg.get("PROXY_URL"):
            S.PROXY.update(url=bcfg["PROXY_URL"], token=bcfg.get("PROXY_TOKEN", ""), branch=br)
        src = bcfg.get("SRC", "")
        n = 0
        for r in S.read_dbf(os.path.join(src, "ARTRN.DBF"),
                            fields={"RECTYP", "DOCNUM", "NETAMT"}):
            if (r.get("RECTYP") or "").strip() not in SALE_RECTYP:
                continue
            k = (br, (r.get("DOCNUM") or "").strip())
            if k in want:
                real[k] = float(r.get("NETAMT") or 0)
                n += 1
        S.log("EXPRESS_AMT: %s เจอยอดจริง %d ใบ" % (br, n))

    updates, same, unmatched = [], 0, []
    for k, b in want.items():
        if k not in real:
            unmatched.append(k)
            continue
        newamt = round(real[k], 2)
        if abs(float(b["amount"] or 0) - newamt) < 0.01 and b.get("amount_source") == "express":
            same += 1
            continue
        updates.append((b["id"], k, newamt, float(b["amount"] or 0)))

    S.log("EXPRESS_AMT: ต้องแก้ %d · ถูกอยู่แล้ว %d · ไม่มี IV ใน Express %d%s"
          % (len(updates), same, len(unmatched), " (DRY)" if dry else ""))
    for _, k, new, old in sorted(updates, key=lambda x: -abs(x[2] - x[3]))[:10]:
        S.log("   %s %s: %.2f -> %.2f (ต่าง %+.2f)" % (k[0], k[1], old, new, new - old))
    if unmatched:
        S.log("   ไม่มี IV ใน Express: %s" % ", ".join("%s/%s" % k for k in unmatched[:5]))
    if dry or not updates:
        return

    for bid, k, newamt, _ in updates:
        S.sb_request(cfg, "PATCH", "/rest/v1/express_bill?id=eq.%d" % bid,
                     {"amount": newamt, "amount_source": "express"},
                     prefer="return=minimal")
    S.log("EXPRESS_AMT: อัปเดต %d แถว OK" % len(updates))


if __name__ == "__main__":
    main()
