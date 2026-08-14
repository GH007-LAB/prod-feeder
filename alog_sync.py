# -*- coding: utf-8 -*-
"""
007 Metals - สะพาน action log: จอ SOPO เดิม (Apps Script) -> sopo_action (Supabase)

ทำไมต้องมี: ระบบกดปิดรายการมี "สองราง" ที่ไม่คุยกัน —
  · จอเดิม (Apps Script "SOPO Mobile"): พนักงานยังกด ✔ อยู่จริงทุกวัน log เก็บใน
    Script Properties (ล่าสุด 400 บรรทัด) + merge ลง action_log.csv บน Drive
  · แอพใหม่ (sopo-app): POST /api/sopo/action -> ตาราง sopo_action ... ซึ่งว่างเปล่า
    เพราะทุกคนยังใช้จอเดิม
สคริปต์นี้ดูดจากรางเดิมเข้าราง Supabase ให้เป็นแหล่งเดียว — แอพใหม่เห็นทันทีว่า
รายการไหนถูกปิดไปแล้วไม่ว่ากดจากที่ไหน และเลิกใช้จอเดิมเมื่อไหร่ก็ไม่เสียประวัติ

กันซ้ำด้วย legacy_key = md5 ของบรรทัด CSV (unique index ใน DB, on_conflict ทิ้งซ้ำ)
แถวที่ import มี employee_id = null (จอเดิมรู้แค่ชื่อเล่นใน who ไม่รู้ตัวตนพนักงาน)

usage: SUPABASE_SERVICE_KEY=<key> python3 alog_sync.py <config_file> [--file path.csv] [--now]
   --file  import ไฟล์ action_log.csv เต็ม (ประวัติทั้งหมด) — ใช้ครั้งแรกครั้งเดียว
   ปกติดึงสดจาก Apps Script ?log=1 (ALOG_URL ใน feeder.env) ชั่วโมงละครั้งจาก run_all.sh
   ต้องใช้ service_role key (sopo_action ให้ authenticated insert ได้เฉพาะในนามตัวเอง
   แถว import ไม่มีตัวตน จึงต้องข้าม RLS) — ไม่มี key = ข้ามเงียบ ๆ
"""
import sys, os, csv, io, json, time, hashlib, datetime, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import so_push as S

EVERY_MIN_DEFAULT = "60"
HEADER = ["ts", "type", "ref", "branch", "who", "role", "status", "note", "codes"]


def rows_from_csv(text):
    """แปลง CSV ของ log เดิม -> รายการ dict สำหรับ insert (ข้าม header/บรรทัดพัง)"""
    out = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 7 or r[0] == "ts":
            continue
        r = (r + [""] * 9)[:9]
        d = dict(zip(HEADER, (x.strip() for x in r)))
        if not d["ref"] or not d["type"]:
            continue
        # ts จอเดิมเป็นเวลาไทย "YYYY-MM-DD HH:MM" -> timestamptz +07:00
        try:
            created = d["ts"].replace(" ", "T") + ":00+07:00"
            datetime.datetime.fromisoformat(created)
        except ValueError:
            created = None
        # กันซ้ำจากบรรทัดเดิม (ts+เนื้อหา) — ต้อง normalize ก่อน hash ให้เท่ากันทุกครั้งที่อ่าน
        key = hashlib.md5("|".join(d[k] for k in HEADER).encode("utf-8")).hexdigest()
        row = {
            "action_type": d["type"],
            "ref": d["ref"],
            "branch": d["branch"] or None,
            "employee_id": None,
            "who": d["who"] or None,
            "role": d["role"] or None,
            "status": d["status"] or "done",
            "note": d["note"] or None,
            "codes": d["codes"] or None,
            "legacy_key": key,
        }
        if created:
            row["created_at"] = created
        out.append(row)
    return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cfg = S.load_config(sys.argv[1])
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (key and cfg.get("SUPABASE_URL")):
        S.log("ALOG: ข้าม — ไม่มี SUPABASE_SERVICE_KEY")
        return
    cfg["SUPABASE_KEY"] = key

    fpath = None
    if "--file" in sys.argv:
        fpath = sys.argv[sys.argv.index("--file") + 1]

    if fpath is None:
        # จับเวลาแบบเดียวกับ express_sync — log ใหม่มีไม่กี่แถว/ชั่วโมง ไม่ต้องถี่กว่านั้น
        stamp = os.path.join(S.state_dir(), "alog_last.txt")
        every = int(os.environ.get("ALOG_EVERY_MIN") or cfg.get("ALOG_EVERY_MIN") or EVERY_MIN_DEFAULT)
        if every > 0 and "--now" not in sys.argv and os.path.exists(stamp):
            if os.path.getmtime(stamp) > time.time() - every * 60:
                return
        open(stamp, "w").write(datetime.datetime.now().isoformat())

    if fpath:
        text = open(fpath, encoding="utf-8-sig").read()
        src = os.path.basename(fpath)
    else:
        url = os.environ.get("ALOG_URL") or cfg.get("ALOG_URL")
        if not url:
            S.log("ALOG: ข้าม — ไม่มี ALOG_URL ใน feeder.env")
            return
        with urllib.request.urlopen(url + ("&" if "?" in url else "?") + "log=1", timeout=60) as resp:
            text = json.loads(resp.read())["csv"]
        src = "apps-script"

    rows = rows_from_csv(text)
    if not rows:
        S.log("ALOG: %s ไม่มีแถวให้ import" % src)
        return
    n = 0
    for batch in S.chunks(rows, 200):
        # on_conflict=legacy_key + ignore-duplicates = แถวเดิมข้ามเงียบ ๆ รันซ้ำได้
        S.sb_request(cfg, "POST", "/rest/v1/sopo_action?on_conflict=legacy_key",
                     batch, prefer="resolution=ignore-duplicates,return=minimal")
        n += len(batch)
    S.log("ALOG: %s -> sopo_action %d แถว (ซ้ำถูกข้ามอัตโนมัติ)" % (src, n))


if __name__ == "__main__":
    main()
