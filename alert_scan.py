# -*- coding: utf-8 -*-
"""สแกน feeder.log หาความผิดปกติ → เขียน ops_alert แล้วเรียก dispatcher บน hr-app ให้ส่ง LINE

รันท้ายทุกรอบของ run_all.sh:
  1. อ่าน log เฉพาะส่วนที่งอกใหม่ (จำ offset ไว้ที่ ~/007so_push/.alert_scan_pos)
  2. บรรทัดที่มี VERIFY-FAIL / SMOKE-FAIL / STORAGE-BACKUP-FAIL / ERROR
     → insert ตาราง ops_alert (ref = hash บรรทัด — ซ้ำแล้วข้าม)
  3. เรียก ALERTS_URL (hr-app /api/cron/alerts, Bearer = service key)
     → ฝั่งนั้นเช็คข้อมูลค้าง/ยอดเงินไม่ตรงเพิ่ม แล้วส่ง LINE แอดมิน

env: SUPABASE_URL, SUPABASE_SERVICE_KEY, ALERTS_URL
"""
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

HOME = os.path.expanduser("~")
LOG_PATH = os.path.join(HOME, "007so_push", "feeder.log")
POS_PATH = os.path.join(HOME, "007so_push", ".alert_scan_pos")

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ALERTS_URL = os.environ.get("ALERTS_URL", "")

# บรรทัดที่ถือว่าผิดปกติ (คำที่สคริปต์ทุกตัวใช้พิมพ์ตอนพัง)
PATTERN = re.compile(r"(VERIFY-FAIL|SMOKE-FAIL|STORAGE-BACKUP-FAIL|ERROR)")


def log(msg):
    print("%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))


def read_new_lines():
    if not os.path.exists(LOG_PATH):
        return []
    size = os.path.getsize(LOG_PATH)
    pos = 0
    if os.path.exists(POS_PATH):
        try:
            pos = int(open(POS_PATH).read().strip() or 0)
        except Exception:
            pos = 0
    if pos > size:  # log ถูกตัด/สร้างใหม่
        pos = 0
    with open(LOG_PATH, "rb") as f:
        f.seek(pos)
        chunk = f.read()
        new_pos = f.tell()
    open(POS_PATH, "w").write(str(new_pos))
    return chunk.decode("utf-8", errors="replace").splitlines()


def push_alerts(rows):
    req = urllib.request.Request(
        URL + "/rest/v1/ops_alert?on_conflict=ref",
        data=json.dumps(rows).encode(),
        method="POST",
        headers={
            "apikey": KEY,
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json",
            "Prefer": "resolution=ignore-duplicates,return=minimal",
        },
    )
    urllib.request.urlopen(req, timeout=30).read()


def main():
    if not URL or not KEY:
        log("ALERTS: ข้าม — env ไม่ครบ")
        return 0

    lines = [l for l in read_new_lines() if PATTERN.search(l)]
    if lines:
        rows = []
        for l in lines[:50]:  # กันเหตุพังถล่ม log — รอบละไม่เกิน 50 เรื่อง
            kind = PATTERN.search(l).group(1)
            ref = "log-" + hashlib.sha1(l.encode()).hexdigest()[:16]
            rows.append({"ref": ref, "kind": kind, "message": l.strip()[:300]})
        try:
            push_alerts(rows)
            log("ALERTS: พบผิดปกติใน log %d บรรทัด → บันทึกแล้ว" % len(rows))
        except Exception as e:
            log("ALERTS: เขียน ops_alert ไม่สำเร็จ: %s" % e)

    # เรียก dispatcher ให้เช็คเพิ่ม (ข้อมูลค้าง/ยอดเงิน) + ส่ง LINE ถ้ามีเรื่องค้าง
    if ALERTS_URL:
        try:
            req = urllib.request.Request(
                ALERTS_URL, headers={"Authorization": "Bearer " + KEY}
            )
            body = urllib.request.urlopen(req, timeout=30).read().decode()
            sent = json.loads(body).get("sent", 0)
            if sent:
                log("ALERTS: ส่ง LINE แล้ว %s เรื่อง" % sent)
        except Exception as e:
            log("ALERTS: เรียก dispatcher ไม่สำเร็จ: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
