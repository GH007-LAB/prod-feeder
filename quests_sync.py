# -*- coding: utf-8 -*-
"""
007 Metals - สร้างรายการงานให้กด ✔ (sopo_snapshot + sopo_item) จาก DBF ตรง

ทำไมต้องมี: เดิม sopo_item ถูก sync รายวันจาก SOPO.html ที่ "เครื่อง Windows สกลนคร"
(task 007SOPOBuild, E:\\007skn) สร้างด้วย build_quests2.py — เครื่องนั้นปิดเมื่อไหร่
รายการบนหน้าแรกของ sopo-app จะหยุดนิ่ง สคริปต์นี้พอร์ตกติกา quest มาสร้างจาก DBF
โดยตรง ทำให้ sopo-app ยืนได้เองไม่พึ่งเครื่องเดิม (และสดกว่า: รายชั่วโมงแทนรายวัน)

กติกา (พอร์ตจาก build_quests2.py — หน้าต่าง = เดือนนี้+เดือนก่อน):
  ขาย    SO มูลค่า >= 50,000 มีเซลล์ ยังไม่ออก IV/HS · ตัดลูกค้าระหว่างสาขา/ออนไลน์
         ที่เกิน 7 วัน (งานปิดเองตามรอบ) · ตัด "ใบเสนอราคาเทียบ" (ลูกค้าเดียวกัน SO
         ห่างกัน <= 3 วัน ใบหนึ่งปิดแล้ว -> ที่เหลือปิดการขายอัตโนมัติ ถ้า >= 20,000
         ยังโชว์เป็นการ์ด auto ให้เซลล์กดยืนยัน)
  ส่งของ SO ที่ออก IV บ้างแล้วแต่ยังส่งไม่หมด (OESO.CMPLDAT ว่าง) มูลค่า >= 30,000
         — ธง "ส่งหมด" ของรายงาน Express มาจาก CMPLDAT (gen_sopo.py:38) แต่ CSV
         ที่จอเดิมอ่านถูก gen วันเดียวแล้วแช่แข็ง SO ที่ส่งครบทีหลังจึงค้างเป็นเควสต์ผี
         (เช่น SO6904369 "ค้าง 39 วัน" ใน brief 10 ส.ค. ทั้งที่ CMPLDAT = 2 ก.ค.)
         — ของเราอ่าน DBF สด ตรงกว่าจอเดิม
  ซื้อ   PO ยังไม่รับของ (ไม่มี CMPLDAT และยังมีบรรทัด REMQTY > 0)
  สต็อก  dead stock > 90 วัน ตัวใหญ่สุด 4 ตัว (ไม่รวมคอยล์ ZZ, >= 3,000)
รายการที่กดปิดแล้ว (sopo_action) ฝั่ง /api/sopo กรองเองตอนแสดง — ที่นี่ไม่ต้องกรอง

usage: SUPABASE_SERVICE_KEY=<key> python3 quests_sync.py <config_file> [--now] [--dry]
   sopo_snapshot/sopo_item เขียนได้เฉพาะ service role (ตาม sopo_schema.sql)
   รันครั้งเดียวต่อรอบจาก run_all.sh คุมจังหวะเองชั่วโมงละครั้ง (QUESTS_EVERY_MIN)
"""
import sys, os, json, time, datetime, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S

BRANCHES = ("BK", "SKN", "PPS")
EVERY_MIN_DEFAULT = "60"
KEEP_SNAPSHOTS = 14  # เท่ากับ /api/sync ของ Vercel
SO_MIN, SHIP_MIN, AUTO_MIN, DEAD_MIN = 50000, 30000, 20000, 3000


def ibol(name):
    u = (name or "").upper().replace("\xa0", " ")
    return ("007" in u) or ("เจ.บี" in u) or ("เจบี" in u) or ("SHOPEE" in u) \
        or ("TIKTOK" in u) or ("LAZADA" in u)


def sb_json(cfg, method, path, payload=None, prefer=None):
    """เหมือน S.sb_request แต่คืน body (ใช้ตอนต้องการ id ของ snapshot ที่เพิ่ง insert)"""
    headers = {"apikey": cfg["SUPABASE_KEY"], "Authorization": "Bearer " + cfg["SUPABASE_KEY"],
               "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(cfg["SUPABASE_URL"].rstrip("/") + path, data=data,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return json.loads(body) if body else None


def month_window(today):
    cur = today.replace(day=1)
    prev = (cur - datetime.timedelta(days=1)).replace(day=1)
    return prev  # วันเริ่มหน้าต่าง (ต้นเดือนก่อน)


def tier_of(age):
    return "bad" if age > 14 else ("mid" if age > 7 else "ok")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = S.load_config(sys.argv[1])
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    dry = "--dry" in sys.argv
    if not dry and not (key and cfg.get("SUPABASE_URL")):
        S.log("QUESTS: ข้าม — ไม่มี SUPABASE_SERVICE_KEY")
        return
    if key:
        cfg["SUPABASE_KEY"] = key
    cfg_dir = os.path.dirname(os.path.abspath(sys.argv[1]))

    stamp = os.path.join(S.state_dir(), "quests_last.txt")
    every = int(os.environ.get("QUESTS_EVERY_MIN") or cfg.get("QUESTS_EVERY_MIN") or EVERY_MIN_DEFAULT)
    if every > 0 and not dry and "--now" not in sys.argv and os.path.exists(stamp):
        if os.path.getmtime(stamp) > time.time() - every * 60:
            return
    if not dry:
        open(stamp, "w").write(datetime.datetime.now().isoformat())

    today = datetime.date.today()
    win = month_window(today)

    # ทำเนียบชื่อเซลล์ (owner บนการ์ด) — สำเนาเดียวกับ sopo-app
    smap = {}
    p = "/Users/cto007/sopo-app/lib/salesperson_map.json"
    if os.path.exists(p):
        for b, codes in json.load(open(p, encoding="utf-8")).items():
            for c, info in codes.items():
                if isinstance(info, dict):
                    smap[(b, c)] = info.get("name", c).split("(")[0].strip()

    items = []
    for br in BRANCHES:
        cf = os.path.join(cfg_dir, "cfg_%s.txt" % br)
        if not os.path.exists(cf):
            continue
        bcfg = S.load_config(cf)
        if bcfg.get("PROXY_URL"):
            S.PROXY.update(url=bcfg["PROXY_URL"], token=bcfg.get("PROXY_TOKEN", ""), branch=br)
        src = bcfg.get("SRC", "")

        names = {}
        for r in S.read_dbf(os.path.join(src, "ARMAS.DBF"), fields={"CUSCOD", "PRENAM", "CUSNAM"}):
            names[r.get("CUSCOD", "")] = ((r.get("PRENAM", "") + " " + r.get("CUSNAM", ""))
                                          .replace("\xa0", " ").strip())

        # IV/HS ที่อ้าง SO (ตัวปิดเควสต์ขาย)
        iv_by_so, hs_by_so = set(), set()
        for r in S.read_dbf(os.path.join(src, "ARTRN.DBF"),
                            fields={"RECTYP", "SONUM", "DOCSTAT"}):
            rt = (r.get("RECTYP") or "").strip()
            if rt not in ("1", "3") or (r.get("DOCSTAT") or "").strip() == "C":
                continue
            sn = (r.get("SONUM") or "").strip()
            if sn:
                (iv_by_so if rt == "3" else hs_by_so).add(sn)

        so_all = {}
        for r in S.read_dbf(os.path.join(src, "OESO.DBF"),
                            fields={"SONUM", "SODAT", "CUSCOD", "SLMCOD", "NETAMT", "CMPLDAT"}):
            dt = r.get("SODAT")
            if not dt or dt < win:
                continue
            sn = (r.get("SONUM") or "").strip()
            v = float(r.get("NETAMT") or 0)
            if not sn or v <= 0:
                continue
            cc = (r.get("CUSCOD") or "").strip()
            so_all[sn] = (names.get(cc, cc), dt, v, (r.get("SLMCOD") or "").strip(),
                          r.get("CMPLDAT") is not None)  # ส่งหมดแล้วไหม (นิยามเดียวกับรายงาน Express)

        # ใบเสนอราคาเทียบ: ลูกค้าเดียวกัน SO ±3 วัน ใบหนึ่งแปลงแล้ว -> ที่เหลือปิดอัตโนมัติ
        byc, comp = {}, set()
        for sn, (cu, dt, v, per, done) in so_all.items():
            if cu:
                byc.setdefault(cu, []).append(sn)
        for cu, sns in byc.items():
            conv = [x for x in sns if x in iv_by_so or x in hs_by_so]
            if not conv or len(sns) < 2:
                continue
            for x in sns:
                if x in iv_by_so or x in hs_by_so:
                    continue
                d0 = so_all[x][1]
                if any(abs((so_all[y][1] - d0).days) <= 3 for y in conv):
                    comp.add(x)

        for sn, (cu, dt, v, per, dlv_done) in so_all.items():
            if not per:
                continue  # ไม่มีเซลล์ = ระหว่างสาขา/ออนไลน์ ไม่ต้องตาม (กติกาเดิม)
            if sn in hs_by_so:
                continue
            age = (today - dt).days
            owner = smap.get((br, per), per) or "—"
            if ibol(cu) and sn not in iv_by_so and age > 7:
                continue
            if sn in comp and sn not in iv_by_so:
                if v >= AUTO_MIN:
                    items.append({"kind": "ขาย", "ref": sn, "branch": br, "owner": owner,
                                  "party": cu[:24], "amount": round(v), "qty": age,
                                  "tier": tier_of(age), "auto": True})
                continue
            if sn not in iv_by_so and v >= SO_MIN:
                items.append({"kind": "ขาย", "ref": sn, "branch": br, "owner": owner,
                              "party": cu[:30], "amount": round(v), "qty": age,
                              "tier": tier_of(age), "auto": False})
            elif sn in iv_by_so and not dlv_done and v >= SHIP_MIN:
                items.append({"kind": "ส่งของ", "ref": sn, "branch": br, "owner": owner,
                              "party": cu[:30], "amount": round(v), "qty": age,
                              "tier": tier_of(age), "auto": False})

        # ---- ซื้อ: PO ยังไม่รับของ ----
        if S._file_exists(os.path.join(src, "POPR.DBF")):
            sup = {}
            for r in S.read_dbf(os.path.join(src, "APMAS.DBF"), fields={"SUPCOD", "PRENAM", "SUPNAM"}):
                sup[r.get("SUPCOD", "")] = ((r.get("PRENAM", "") + " " + r.get("SUPNAM", ""))
                                            .replace("\xa0", " ").strip())
            po_rem = {}
            for r in S.read_dbf(os.path.join(src, "POPRIT.DBF"), fields={"PONUM", "REMQTY"}):
                pn = (r.get("PONUM") or "").strip()
                po_rem[pn] = po_rem.get(pn, 0.0) + max(float(r.get("REMQTY") or 0), 0.0)
            for r in S.read_dbf(os.path.join(src, "POPR.DBF"),
                                fields={"PONUM", "PODAT", "SUPCOD", "NETAMT", "CMPLDAT", "DOCSTAT"}):
                dt = r.get("PODAT")
                if not dt or dt < win:
                    continue
                v = float(r.get("NETAMT") or 0)
                pn = (r.get("PONUM") or "").strip()
                if not pn or v <= 0 or (r.get("DOCSTAT") or "").strip() == "C":
                    continue
                received = r.get("CMPLDAT") is not None or po_rem.get(pn, 1.0) <= 0
                if received:
                    continue
                age = (today - dt).days
                items.append({"kind": "ซื้อ", "ref": pn, "branch": br, "owner": "ทีมจัดซื้อ",
                              "party": sup.get((r.get("SUPCOD") or "").strip(), "")[:30],
                              "amount": round(v), "qty": age, "tier": tier_of(age), "auto": False})

    # ---- สต็อก: dead stock ตัวใหญ่สุด (จาก sopo_dead_stock ที่ dead_stock.py เขียนไว้) ----
    dead = sb_json(cfg, "GET", "/rest/v1/sopo_dead_stock?select=branch,stkcod,stkdes,totval"
                               "&order=totval.desc&limit=60") or []
    n_dead = 0
    for d in dead:
        if n_dead >= 4:
            break
        if (d.get("stkcod") or "").upper().startswith("ZZ") or float(d.get("totval") or 0) < DEAD_MIN:
            continue
        items.append({"kind": "สต็อก", "ref": (d.get("stkdes") or "").replace("\xa0", " ")[:18],
                      "branch": d["branch"], "owner": "ดรีม", "party": None,
                      "amount": round(float(d["totval"])), "qty": 0, "tier": "bad", "auto": False})
        n_dead += 1

    S.log("QUESTS: %d รายการ (ขาย %d · ส่งของ %d · ซื้อ %d · สต็อก %d)%s" % (
        len(items),
        sum(1 for i in items if i["kind"] == "ขาย"),
        sum(1 for i in items if i["kind"] == "ส่งของ"),
        sum(1 for i in items if i["kind"] == "ซื้อ"),
        n_dead, " (DRY)" if dry else ""))
    if "--dump" in sys.argv:  # debug: เขียนรายการเต็มออกไฟล์ไว้ diff กับ snapshot เดิม
        json.dump(items, open(os.path.join(S.state_dir(), "quests_dump.json"), "w",
                              encoding="utf-8"), ensure_ascii=False)
    if dry or not items:
        return

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    snap = sb_json(cfg, "POST", "/rest/v1/sopo_snapshot",
                   {"as_of": now_iso, "item_count": len(items), "source": "feeder"},
                   prefer="return=representation")
    snap_id = snap[0]["id"]
    for batch in S.chunks([dict(x, snapshot_id=snap_id, payload={}, as_of=now_iso)
                           for x in items], 200):
        S.sb_request(cfg, "POST", "/rest/v1/sopo_item", batch, prefer="return=minimal")

    # เก็บ snapshot ล่าสุด KEEP_SNAPSHOTS ชุด (ตัวเก่าลบทิ้ง — sopo_item cascade ตาม)
    old = sb_json(cfg, "GET", "/rest/v1/sopo_snapshot?select=id&order=as_of.desc&offset=%d"
                              % KEEP_SNAPSHOTS) or []
    if old:
        S.sb_request(cfg, "DELETE", "/rest/v1/sopo_snapshot?id=in.(%s)"
                     % ",".join(str(o["id"]) for o in old))
    S.log("QUESTS: snapshot %d push OK (ลบเก่า %d ชุด)" % (snap_id, len(old)))


if __name__ == "__main__":
    main()
