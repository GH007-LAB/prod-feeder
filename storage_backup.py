#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
storage_backup.py — สำรองไฟล์ Supabase Storage ลงเครื่อง Mac mini (incremental) วันละครั้ง
=========================================================================
ทำไมต้องมี: backup รายวันของ Supabase ครอบเฉพาะฐานข้อมูล "ไม่รวมไฟล์ Storage"
(หน้า Backups เขียนไว้ตรง ๆ) — รูปชั่งกิโล/สลิปน้ำมัน/รูปงานซ่อม/รูปพนักงาน
จึงไม่มีสำเนาเลย ตัวนี้ mirror ทุก bucket ลง ~/007backup/storage/<bucket>/...

- incremental: ดาวน์โหลดเฉพาะไฟล์ใหม่/ขนาดเปลี่ยน (เทียบ manifest.json)
- ไม่ลบไฟล์ฝั่งเรา แม้ต้นทางถูกลบ (backup ต้องกันลบพลาดด้วย)
- จังหวะ: STORAGE_BACKUP_EVERY_MIN (แนะนำ 1440 = วันละครั้ง)
- log: STORAGE-BACKUP: ... / STORAGE-BACKUP-FAIL: ... ใน feeder.log
env: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
EVERY_MIN = int(os.environ.get("STORAGE_BACKUP_EVERY_MIN") or 1440)
ROOT = os.path.expanduser("~/007backup/storage")
STAMP = os.path.expanduser("~/007so_push/.storage_backup_last_run")
MANIFEST = os.path.join(ROOT, "manifest.json")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def throttle_ok():
    if EVERY_MIN <= 0:
        return True
    try:
        if time.time() - os.path.getmtime(STAMP) < EVERY_MIN * 60 - 60:
            return False
    except OSError:
        pass
    return True


def api(method, path, body=None, raw=False):
    headers = {"apikey": KEY, "Authorization": "Bearer " + KEY}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    r = urllib.request.Request(URL + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=120) as res:
        payload = res.read()
        return payload if raw else json.loads(payload.decode() or "null")


def walk_bucket(bucket, prefix=""):
    """เดินทุกโฟลเดอร์ใน bucket -> yield (path, size, updated_at) ของไฟล์จริง"""
    offset = 0
    while True:
        items = api("POST", f"/storage/v1/object/list/{bucket}",
                    {"prefix": prefix, "limit": 1000, "offset": offset,
                     "sortBy": {"column": "name", "order": "asc"}})
        if not items:
            return
        for it in items:
            name = it.get("name") or ""
            full = f"{prefix}{name}" if not prefix else f"{prefix}/{name}"
            if it.get("id") is None:  # โฟลเดอร์ -> เดินต่อ
                yield from walk_bucket(bucket, full)
            else:
                meta = it.get("metadata") or {}
                yield full, meta.get("size") or 0, it.get("updated_at") or ""
        if len(items) < 1000:
            return
        offset += 1000


def main():
    if not (URL and KEY):
        log("STORAGE-BACKUP: ข้าม — env ไม่ครบ")
        return
    if not throttle_ok():
        return
    open(STAMP, "w").close()
    os.makedirs(ROOT, exist_ok=True)
    try:
        manifest = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}
    except ValueError:
        manifest = {}

    try:
        buckets = [b["name"] for b in api("GET", "/storage/v1/bucket")]
    except Exception as e:  # noqa: BLE001
        log(f"STORAGE-BACKUP-FAIL: อ่านรายชื่อ bucket ไม่ได้: {e}")
        sys.exit(1)

    total = new = failed = 0
    new_bytes = 0
    for b in buckets:
        try:
            for path, size, updated in walk_bucket(b):
                total += 1
                mkey = f"{b}/{path}"
                dest = os.path.join(ROOT, b, path)
                if manifest.get(mkey) == [size, updated] and os.path.exists(dest):
                    continue
                try:
                    blob = api("GET", f"/storage/v1/object/{b}/{urllib.request.quote(path)}", raw=True)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(blob)
                    manifest[mkey] = [size, updated]
                    new += 1
                    new_bytes += len(blob)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    log(f"STORAGE-BACKUP: โหลดไม่ได้ {mkey}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            log(f"STORAGE-BACKUP: เดิน bucket {b} ไม่ได้: {e}")

    json.dump(manifest, open(MANIFEST, "w"))
    line = (f"STORAGE-BACKUP: {len(buckets)} buckets · ไฟล์ทั้งหมด {total} · "
            f"ใหม่ {new} ({new_bytes/1048576:.1f} MB) · พลาด {failed} -> {ROOT}")
    if failed:
        log(line.replace("STORAGE-BACKUP:", "STORAGE-BACKUP-FAIL:"))
        sys.exit(1)
    log(line)


if __name__ == "__main__":
    main()
